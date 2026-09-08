"""Taint tracking — control #7 in the injection-defense stack (docs/06 §6).

The rule from the trust model (docs/02 §6): data read out of a file, a web page,
or any tool's stdout is **T2 UNTRUSTED**. It may be shown to the user and it may
be summarised. It may never silently become a *parameter of a later action*,
because that is how a malicious document launders itself into `dest_dir`.

This module answers one question per action:

    given which verbs produced the variables this action consumes,
    how trusted are this action's inputs?

The answer is attached to the action (`Action.trust`) and consumed by the
deterministic scorer, which escalates to R2 when T2 data reaches a sensitive
argument. It is pure static analysis over the plan DAG — no LLM, no execution.
"""

from __future__ import annotations

from dataclasses import dataclass

from maestro import registry
from maestro.ir import Action, Plan, Trust

# Verbs whose *output* is untrusted content by construction. Everything these
# produce is attacker-controllable in the threat model.
UNTRUSTED_PRODUCERS: frozenset[str] = frozenset(
    {
        "fs.read_text",
        "browser.extract",
        "search.by_content",
    }
)

# Verbs whose output is a *list of paths MAESTRO itself enumerated*. The names
# inside are attacker-influenced (a filename is untrusted, docs/02 §6 rule 5)
# but the paths were produced by our own glob under an allowlisted root, so
# they are T1 for the purpose of feeding a later fs verb. Shell interpolation
# never happens anywhere, which is what makes this safe.
DERIVED_PRODUCERS: frozenset[str] = frozenset(
    {
        "fs.glob",
        "fs.stat",
        "fs.mkdir",
        "fs.copy",
        "fs.move_batch",
        "search.by_name",
        "sys.info",
    }
)


@dataclass(frozen=True)
class TaintReport:
    action_id: str
    trust: Trust
    tainted_args: tuple[str, ...]  # sensitive args reached by T2 data
    sources: tuple[str, ...]  # the producing action ids


def producer_trust(verb: str) -> Trust:
    if verb in UNTRUSTED_PRODUCERS:
        return Trust.T2
    if verb in DERIVED_PRODUCERS:
        return Trust.T1
    # Unknown / future verbs: assume the worst. Fail closed.
    return Trust.T2


def analyze(plan: Plan) -> dict[str, TaintReport]:
    """Propagate trust through the DAG in topological order."""
    var_trust: dict[str, Trust] = {}
    var_owner: dict[str, str] = {}
    reports: dict[str, TaintReport] = {}

    for aid in plan.topo_order:
        action = plan.action(aid)
        refs = sorted(action.var_refs())
        in_trust = Trust.T0 if not refs else max(
            (var_trust.get(r, Trust.T2) for r in refs), default=Trust.T0
        )
        sources = tuple(var_owner[r] for r in refs if r in var_owner)

        tainted = tuple(_sensitive_args_reached(action, refs, var_trust))
        reports[aid] = TaintReport(aid, in_trust, tainted, sources)

        if action.produces:
            # An action's output is as untrusted as the worse of (its inputs,
            # what the verb itself produces).
            var_trust[action.produces] = max(in_trust, producer_trust(action.verb))
            var_owner[action.produces] = aid

    return reports


def apply(plan: Plan) -> dict[str, TaintReport]:
    """Analyze and stamp `Action.trust` in place, so the label survives into
    the audit log, the episode row, and the dataset record."""
    reports = analyze(plan)
    for a in plan.actions:
        a.trust = reports[a.action_id].trust
    return reports


def _sensitive_args_reached(
    action: Action, refs: list[str], var_trust: dict[str, Trust]
) -> list[str]:
    """Which of this verb's *sensitive* args are fed by T2 data."""
    try:
        spec = registry.get(action.verb)
    except registry.RegistryError:
        return []
    hits: list[str] = []
    for arg_name in spec.sensitive_args:
        value = action.args.get(arg_name)
        for ref in _refs_in(value):
            if var_trust.get(ref, Trust.T2) is Trust.T2:
                hits.append(arg_name)
                break
    return hits


def _refs_in(value) -> list[str]:
    from maestro.ir.model import VAR_RE

    out: list[str] = []
    if isinstance(value, str):
        m = VAR_RE.match(value)
        if m:
            out.append(m.group(1))
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_refs_in(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_refs_in(v))
    return out
