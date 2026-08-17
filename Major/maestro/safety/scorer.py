"""Deterministic risk scoring — the heart of the safety layer.

No LLM is called anywhere in this module. Given the same plan, the same
verdicts come out, every time (NFR-07). Three invariants, tested in
tests/test_scorer.py:

  1. deterministic       — pure function of (plan, policy)
  2. monotonic           — every rule may only RAISE risk, never lower it
  3. fail-closed         — unknown verb / bad args / bad path => BLOCKED

The planner's `risk_hint` is recorded for the hint-agreement metric
(docs/05 §3) and then ignored for all decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from maestro import registry
from maestro.ir import Action, Plan, Risk
from maestro.safety.paths import PathPolicy, PathVerdict

# Touching more than this many files in one action is consequential even
# when each individual touch is reversible.
BULK_N = 25


@dataclass
class ActionVerdict:
    action_id: str
    verb: str
    risk: Risk
    reasons: list[str] = field(default_factory=list)
    hint: Risk | None = None  # what the planner guessed; measured, never trusted

    @property
    def blocked(self) -> bool:
        return self.risk == Risk.BLOCKED


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
    def gate(self) -> str:
        if self.blocked:
            return "refuse"
        return {
            Risk.R0: "auto",
            Risk.R1: "auto",
            Risk.R2: "confirm",
            Risk.R3: "typed_confirm",
        }[self.risk]


def _raise_to(current: Risk, new: Risk, reasons: list[str], why: str) -> Risk:
    """Monotonic escalation: record the reason, never go down."""
    if new > current:
        reasons.append(why)
        return new
    return current


def score_action(
    action: Action, policy: PathPolicy, *, estimated_files: int | None = None
) -> ActionVerdict:
    reasons: list[str] = []

    # Fail closed on anything the registry rejects.
    try:
        spec = registry.get(action.verb)
        registry.validate_args(action.verb, _strip_vars(action.args))
    except registry.RegistryError as e:
        return ActionVerdict(
            action.action_id, action.verb, Risk.BLOCKED, [str(e)], action.risk_hint
        )

    if spec.hard_blocked:
        return ActionVerdict(
            action.action_id,
            action.verb,
            Risk.BLOCKED,
            [f"{action.verb} is hard-blocked by policy; no override exists"],
            action.risk_hint,
        )

    risk = spec.base_risk
    reasons.append(f"base risk of {action.verb} is {spec.base_risk}")

    # Path rules — denylist beats everything, non-workspace escalates.
    for arg_name in spec.path_args:
        value = action.args.get(arg_name)
        for p in _iter_paths(value):
            verdict = policy.check(p)
            if verdict == PathVerdict.DENIED:
                return ActionVerdict(
                    action.action_id,
                    action.verb,
                    Risk.BLOCKED,
                    reasons + [f"path {p!r} is denylisted or unresolvable"],
                    action.risk_hint,
                )
            if verdict == PathVerdict.OUTSIDE:
                risk = _raise_to(risk, Risk.R2, reasons, f"path {p!r} outside workspace")

    # Irreversible state change can never sit below R3.
    if not spec.reversible and spec.base_risk > Risk.R0 and action.undo is None:
        risk = _raise_to(risk, Risk.R3, reasons, "irreversible and no undo declared")

    # Bulk operations are consequential even when reversible.
    if estimated_files is not None and estimated_files > BULK_N:
        risk = _raise_to(risk, Risk.R2, reasons, f"touches {estimated_files} files (> {BULK_N})")

    return ActionVerdict(action.action_id, action.verb, risk, reasons, action.risk_hint)


def score_plan(plan: Plan, policy: PathPolicy | None = None) -> PlanVerdict:
    policy = policy or PathPolicy()
    return PlanVerdict(plan.plan_id, [score_action(a, policy) for a in plan.actions])


def _strip_vars(args: dict) -> dict:
    """Replace $var references with placeholders for schema validation.

    A $var's actual value is only known at execution time; the executor
    re-validates after binding. Here we only check the *shape* of the args.
    """
    from maestro.ir.model import VAR_RE

    def walk(v):
        if isinstance(v, str) and VAR_RE.match(v):
            return []  # placeholder: bound values are lists/paths at runtime
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return {k: walk(v) for k, v in args.items()}


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
