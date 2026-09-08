"""The consent gate (docs/06-SAFETY-SPEC.md §4).

Three things live here, and none of them is a print statement:

1. `ConsentRequest` — what the user is shown. Built from the dry run, so the
   preview is *evidence*, not a restatement of the instruction.
2. The typed-confirmation token for R3. Deliberate friction, and it must be
   derived from the plan so that approving one plan can never approve another.
3. Anti-habituation state: `Approval.remember` lets a *specific shape of plan*
   auto-approve for the rest of the session. The shape is the canonical
   fingerprint of the plan plus its risk tier — so "move PDFs to Invoices"
   approved once does not also approve "move PDFs to /Volumes/USB".

FCR (docs/07 §2) is measured against this module: every time we ask and did not
need to, that is a false confirmation, and the mechanism erodes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from maestro.executor.base import EffectManifest
from maestro.ir import Plan, Risk
from maestro.safety.scorer import PlanVerdict


@dataclass(frozen=True)
class ConsentRequest:
    plan: Plan
    verdict: PlanVerdict
    manifests: list[EffectManifest]

    @property
    def gate(self) -> str:
        return self.verdict.gate

    @property
    def token(self) -> str | None:
        """Typed-confirmation token for R3 (docs/06 §4). None below R3."""
        if self.verdict.risk < Risk.R3:
            return None
        files = sum(m.files_touched for m in self.manifests)
        noun = "FILES" if files != 1 else "FILE"
        return f"CONFIRM {files} {noun}" if files else "CONFIRM HIGH RISK"

    @property
    def shape(self) -> str:
        """Session-scoped auto-approve key. Same shape == same consent."""
        return f"{self.plan.fingerprint()}:{self.verdict.risk}"


@dataclass
class Approval:
    approved: bool
    method: str = "click"  # click | typed | auto | remembered | denied
    remember: bool = False
    note: str = ""


ConsentFn = Callable[[ConsentRequest], Approval]


@dataclass
class ConsentGate:
    """Wraps a UI callback with the policy the UI is not allowed to bend."""

    ask: ConsentFn | None = None
    _remembered: set[str] = field(default_factory=set)

    def decide(self, req: ConsentRequest) -> Approval:
        gate = req.gate

        if gate == "refuse":
            return Approval(False, "denied", note="hard-blocked by policy")

        if gate == "auto":
            # R0/R1 never prompt. Prompting here is the habituation failure
            # mode that docs/06 §4 calls out explicitly.
            return Approval(True, "auto")

        if req.shape in self._remembered:
            return Approval(True, "remembered")

        if self.ask is None:
            # No UI attached => cannot obtain consent => do not execute.
            return Approval(False, "denied", note="no consent channel available")

        answer = self.ask(req)

        if answer.approved and gate == "typed_confirm" and answer.method != "typed":
            # A click cannot satisfy an R3 gate, whatever the UI claims.
            return Approval(False, "denied", note="R3 requires typed confirmation")

        if answer.approved and answer.remember:
            self._remembered.add(req.shape)
        return answer

    def forget_all(self) -> None:
        self._remembered.clear()


def token_matches(typed: str, expected: str) -> bool:
    """Case- and whitespace-insensitive, but the words must be right."""
    norm = lambda s: re.sub(r"\s+", " ", s.strip().upper())  # noqa: E731
    return norm(typed) == norm(expected)
