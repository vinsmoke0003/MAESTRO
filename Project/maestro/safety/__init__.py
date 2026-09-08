"""L3 — the safety / policy engine. The project's research contribution.

    paths     allow/deny with canonicalisation      (docs/06 §2)
    taint     T0/T1/T2 propagation over the DAG     (docs/06 §6 control 7)
    scorer    deterministic R0-R3 risk scoring      (docs/06 §2)
    consent   the gate, typed confirmation, anti-habituation (docs/06 §4)
    budget    T7 resource-exhaustion guard          (docs/06 §1)
    audit     hash-chained tamper-evident log       (docs/02 §8)

Nothing in this package calls an LLM. That is the point (NFR-07).
"""

from maestro.safety.audit import AuditLog
from maestro.safety.budget import BudgetExceeded, BudgetGuard
from maestro.safety.consent import Approval, ConsentGate, ConsentRequest, token_matches
from maestro.safety.paths import PathPolicy, PathVerdict
from maestro.safety.scorer import ActionVerdict, PlanVerdict, score_action, score_plan
from maestro.safety.taint import TaintReport
from maestro.safety.taint import analyze as analyze_taint

__all__ = [
    "AuditLog",
    "BudgetExceeded",
    "BudgetGuard",
    "Approval",
    "ConsentGate",
    "ConsentRequest",
    "token_matches",
    "PathPolicy",
    "PathVerdict",
    "ActionVerdict",
    "PlanVerdict",
    "score_action",
    "score_plan",
    "TaintReport",
    "analyze_taint",
]
