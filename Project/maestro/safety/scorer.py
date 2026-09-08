"""Deterministic risk scoring — the heart of the safety layer.

No LLM is called anywhere in this module. Given the same plan, the same
verdicts come out, every time (NFR-07). Three invariants, pinned by
tests/test_scorer.py:

  1. deterministic       — pure function of (plan, policy, config)
  2. monotonic           — every rule may only RAISE risk, never lower it
  3. fail-closed         — unknown verb / bad args / bad path => BLOCKED

The planner's `risk_hint` is recorded for the hint-agreement metric
(docs/05 §3) and then ignored for all decisions.

Rule order mirrors docs/06 §2's `score_risk` pseudocode exactly, so the report
can print the two side by side.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from maestro import registry
from maestro.config import settings
from maestro.ir import Action, Plan, Risk, Trust
from maestro.safety import taint as taint_mod
from maestro.safety.paths import PathPolicy, PathVerdict

# Touching more than this many files in one action is consequential even
# when each individual touch is reversible. Overridable via MAESTRO_BULK_N
# because the ablation harness sweeps it.
BULK_N_DEFAULT = 25


@dataclass
class ActionVerdict:
    action_id: str
    verb: str
    risk: Risk
    reasons: list[str] = field(default_factory=list)
    hint: Risk | None = None  # what the planner guessed; measured, never trusted
    trust: Trust = Trust.T1
    rules_fired: list[str] = field(default_factory=list)  # rule ids, for the report

    @property
    def blocked(self) -> bool:
        return self.risk == Risk.BLOCKED

    @property
    def hint_agrees(self) -> bool | None:
        """Risk-hint agreement (docs/05 §3). None when the planner gave no hint."""
        if self.hint is None:
            return None
        return self.hint == self.risk


@dataclass
class PlanVerdict:
    plan_id: str
    actions: list[ActionVerdict]

    @property
    def risk(self) -> Risk:
        """Plan risk = max over actions. The user approves a plan, not steps."""
        return max((a.risk for a in self.actions), default=Risk.R0)

    @property
    def blocked(self) -> bool:
        return any(a.blocked for a in self.actions)

    @property
    def block_reasons(self) -> list[str]:
        return [r for a in self.actions if a.blocked for r in a.reasons]

    @property
    def gate(self) -> str:
        """auto | confirm | typed_confirm | refuse (docs/06 §4)."""
        if self.blocked:
            return "refuse"
        return {
            Risk.R0: "auto",
            Risk.R1: "auto",
            Risk.R2: "confirm",
            Risk.R3: "typed_confirm",
        }[self.risk]

    @property
    def first_control(self) -> str | None:
        """Which control fired first — the per-attack attribution column that
        docs/06 §6 and docs/07 §7 Table 6 ask for."""
        for a in self.actions:
            if a.blocked and a.rules_fired:
                return a.rules_fired[-1]
        for a in self.actions:
            if a.risk >= Risk.R2 and a.rules_fired:
                return a.rules_fired[-1]
        return None

    def action_verdict(self, action_id: str) -> ActionVerdict:
        for a in self.actions:
            if a.action_id == action_id:
                return a
        raise KeyError(action_id)


# --------------------------------------------------------------------------- #
# the scoring function
# --------------------------------------------------------------------------- #


def _raise_to(current: Risk, new: Risk, verdict_bits: tuple[list[str], list[str]],
              why: str, rule: str) -> Risk:
    """Monotonic escalation: record the reason and the rule id, never go down."""
    reasons, rules = verdict_bits
    if new > current:
        reasons.append(why)
        rules.append(rule)
        return new
    return current


def score_action(
    action: Action,
    policy: PathPolicy,
    *,
    estimated_files: int | None = None,
    trust: Trust | None = None,
    tainted_args: tuple[str, ...] = (),
    bulk_n: int | None = None,
) -> ActionVerdict:
    reasons: list[str] = []
    rules: list[str] = []
    bits = (reasons, rules)
    bulk_n = bulk_n if bulk_n is not None else settings().bulk_n
    trust = trust if trust is not None else action.trust

    # RULE 1 — closed registry + typed args. Fail closed on anything rejected.
    try:
        spec = registry.get(action.verb)
        registry.validate_args(action.verb,
                               _strip_vars(action.args, action.verb))
    except registry.RegistryError as e:
        return ActionVerdict(
            action.action_id, action.verb, Risk.BLOCKED, [str(e)], action.risk_hint,
            trust, ["C4_closed_registry"],
        )

    # RULE 2 — hard blocks. No override path exists (docs/06 §5).
    if spec.hard_blocked:
        return ActionVerdict(
            action.action_id, action.verb, Risk.BLOCKED, [spec.blocked_reason],
            action.risk_hint, trust, ["C6_hard_block"],
        )

    risk = spec.base_risk
    reasons.append(f"base risk of {action.verb} is {spec.base_risk}")
    rules.append("R_base")

    # RULE 3 — path policy. Denylist beats everything; non-workspace escalates.
    for arg_name in spec.path_args:
        value = action.args.get(arg_name)
        for p in _iter_paths(value):
            verdict = policy.check(p)
            if verdict is PathVerdict.DENIED:
                return ActionVerdict(
                    action.action_id, action.verb, Risk.BLOCKED,
                    reasons + [f"path {p!r} is denylisted or unresolvable"],
                    action.risk_hint, trust, rules + ["C5_path_denylist"],
                )
            if verdict is PathVerdict.OUTSIDE:
                risk = _raise_to(risk, Risk.R2, bits,
                                 f"path {p!r} is outside the declared workspace",
                                 "R_outside_workspace")

    # RULE 4 — irreversible state change can never sit below R3.
    if not spec.reversible and spec.base_risk > Risk.R0 and action.undo is None:
        risk = _raise_to(risk, Risk.R3, bits, "irreversible and no undo declared",
                         "R_irreversible")

    # RULE 5 — bulk operations are consequential even when reversible.
    if estimated_files is not None and estimated_files > bulk_n:
        risk = _raise_to(risk, Risk.R2, bits,
                         f"touches {estimated_files} files (> {bulk_n})", "R_bulk")

    # RULE 6 — any write that leaves this machine.
    if spec.network_write:
        risk = _raise_to(risk, Risk.R2, bits, "writes to an external network destination",
                         "R_network_write")

    # RULE 7 — taint: untrusted content reaching a sensitive argument.
    #
    # Recorded even when it does not raise the tier. `fs.move_batch` is already
    # R2, so a laundered dest_dir would otherwise escalate nothing and leave no
    # trace — and then the preview would not warn the user, and the
    # "which control fired" attribution in docs/06 §6 would silently omit C7.
    # Risk is still monotonic; only the explanation is unconditional.
    if tainted_args:
        why = ("argument(s) " + ", ".join(tainted_args)
               + " derived from UNTRUSTED content")
        risk = _raise_to(risk, Risk.R2, bits, why, "C7_taint")
        if why not in reasons:
            reasons.append(why)
            rules.append("C7_taint")
    elif trust is Trust.T2:
        why = "inputs include UNTRUSTED content"
        risk = _raise_to(risk, Risk.R2, bits, why, "C7_taint")
        if why not in reasons:
            reasons.append(why)
            rules.append("C7_taint")

    return ActionVerdict(action.action_id, action.verb, risk, reasons,
                         action.risk_hint, trust, rules)


def score_plan(
    plan: Plan,
    policy: PathPolicy | None = None,
    *,
    file_estimates: dict[str, int] | None = None,
    bulk_n: int | None = None,
) -> PlanVerdict:
    """Score every action. `file_estimates` comes from the dry run when it has
    already happened; the orchestrator re-scores with it so the bulk rule can
    fire on real counts rather than on nothing."""
    policy = policy or PathPolicy()
    file_estimates = file_estimates or {}
    reports = taint_mod.apply(plan)
    verdicts = [
        score_action(
            a,
            policy,
            estimated_files=file_estimates.get(a.action_id),
            trust=reports[a.action_id].trust,
            tainted_args=reports[a.action_id].tainted_args,
            bulk_n=bulk_n,
        )
        for a in plan.actions
    ]
    return PlanVerdict(plan.plan_id, verdicts)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _strip_vars(args: dict, verb: str | None = None) -> dict:
    """Replace `$var` references with type-appropriate placeholders.

    A `$var`'s actual value is only known at execution time; the executor
    re-validates after binding. Here we only check the *shape* of the literal
    args, so an unbound reference must not itself look like a type error.

    The placeholder has to match the field's declared type. Substituting `[]`
    for everything — as the first version did — meant a variable in a
    string-typed field (`dest_dir: "$content"`, which is exactly the laundering
    case the taint tracker exists to catch) failed validation and the action was
    BLOCKED as "invalid args". Fail-closed, so not unsafe, but wrong: the plan
    was legal, the diagnosis was misleading, and the taint rule never got to run.
    """
    from maestro.ir.model import VAR_RE

    placeholders: dict[str, object] = {}
    if verb is not None:
        try:
            model = registry.get(verb).args_model
        except registry.RegistryError:
            model = None
        if model is not None:
            for name, field in model.model_fields.items():
                placeholders[name] = _placeholder_for(field.annotation)

    def walk(v, field_name: str | None = None):
        if isinstance(v, str) and VAR_RE.match(v):
            return placeholders.get(field_name, [])
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x, field_name) for x in v]
        return v

    return {k: walk(v, k) for k, v in args.items()}


def _placeholder_for(annotation) -> object:
    """A value that satisfies `annotation` without asserting anything about it."""
    text = str(annotation)
    if "list" in text:
        return []
    if "dict" in text:
        return {}
    if "bool" in text:
        return False
    if "int" in text:
        return 0
    if "float" in text:
        return 0.0
    return ""  # str, Literal[...], and anything else string-shaped


def _iter_paths(value) -> list[str]:
    from maestro.ir.model import VAR_RE

    if value is None:
        return []
    if isinstance(value, str):
        return [] if VAR_RE.match(value) else [value]
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            out.extend(_iter_paths(v))
        return out
    return []
