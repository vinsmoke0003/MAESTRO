"""The LLM planner: natural language -> validated Action IR (docs/02 §4, L4).

Trust boundaries, enforced structurally rather than by prompting:

* The model emits ONLY the actions array. `plan_id`, the instruction and its
  hash are bound by US, so a plan is always traceable to the exact user input.
  A model that tried to rewrite the instruction it is answering could not.
* The verb enum inside the decoding schema comes from the closed registry — the
  model cannot even *emit* an unregistered verb.
* Whatever comes back still goes through `Plan.model_validate`, a second
  registry membership check, and then the deterministic scorer. The planner
  proposes; it never decides (docs/06 §1 principle 1).
* Repair loop: structural errors are fed back verbatim, bounded at
  `MAX_ATTEMPTS`, then it fails cleanly (FR-11). No silent retries, no partial
  plans, and — importantly — no falling back to "just do something".

`HybridPlanner` is what the product actually runs: it tries the LLM and falls
back to the deterministic template planner when there is no model, when the
model is unreachable, or when three repair attempts still produce garbage. That
fallback is why MAESTRO works on a clean clone, and it is also an evaluation
condition in its own right (the A6 ablation).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from maestro import registry
from maestro.ir import Plan, PlannerInfo, Risk
from maestro.llm.base import LLMClient, LLMError
from maestro.nlp.entities import Slots
from maestro.planner import prompts
from maestro.planner.rulebased import NoTemplate, build_plan

MAX_ATTEMPTS = 3

_RISK_NAMES = {"R0": Risk.R0, "R1": Risk.R1, "R2": Risk.R2, "R3": Risk.R3}


class PlannerError(Exception):
    """Planning failed after all repair attempts — surface it, never guess."""


@dataclass
class PlanAttempt:
    n: int
    raw: str
    error: str | None
    ms: float


@dataclass
class Planner:
    """LLM planner with a bounded, transparent repair loop."""

    client: LLMClient
    home: Path = field(default_factory=Path.home)
    max_attempts: int = MAX_ATTEMPTS
    exemplars: list[tuple[str, dict]] | None = None
    name: str = "llm"
    attempts: list[PlanAttempt] = field(default_factory=list)

    @property
    def model(self) -> str:
        return getattr(self.client, "model", "unknown")

    def plan(self, instruction: str, intent: str | None = None,
             slots: Slots | None = None) -> Plan:
        system = prompts.system_prompt()
        user = prompts.user_prompt(instruction, intent, slots, self.home, self.exemplars)
        schema = prompts.decode_schema()
        self.attempts = []
        last_error = "no attempt was made"

        for n in range(1, self.max_attempts + 1):
            t0 = time.perf_counter()
            try:
                raw = self.client.chat(system, user, schema=schema)
            except LLMError as e:
                raise PlannerError(f"LLM unavailable: {e}") from e
            ms = (time.perf_counter() - t0) * 1000

            try:
                plan = self._build(instruction, raw)
            except (json.JSONDecodeError, ValidationError, ValueError,
                    registry.RegistryError) as e:
                last_error = _clean_error(e)
                self.attempts.append(PlanAttempt(n, raw, last_error, ms))
                user = prompts.repair_prompt(instruction, raw, last_error, intent, slots,
                                             self.home)
                continue

            self.attempts.append(PlanAttempt(n, raw, None, ms))
            return plan

        raise PlannerError(
            f"no schema-valid plan after {self.max_attempts} attempts; "
            f"last error: {last_error}"
        )

    # -- internals ---------------------------------------------------------

    def _build(self, instruction: str, raw: str) -> Plan:
        data = json.loads(_strip_fences(raw))
        plan = Plan.model_validate({
            "plan_id": f"p_{uuid.uuid4().hex[:8]}",
            "instruction": instruction,  # bound by us, never by the model
            "planner": PlannerInfo(model=self.model, version="1.0.0",
                                   strategy="llm").model_dump(),
            "actions": self._adapt(data.get("actions", [])),
        })
        # Registry membership, again. Constrained decoding should make this
        # unreachable — but the planner must not RELY on the decoder.
        for a in plan.actions:
            spec = registry.get(a.verb)
            if spec.hard_blocked:
                raise ValueError(
                    f"{a.action_id} uses {a.verb}, which is hard-blocked and was never "
                    "offered to you; use the reversible alternative"
                )
        return plan

    @staticmethod
    def _adapt(actions: list) -> list[dict]:
        """Map the model's output onto strict Action fields (drop nulls etc.)."""
        out = []
        for i, a in enumerate(actions, start=1):
            if not isinstance(a, dict):
                raise ValueError(f"action {i} is not an object")
            item: dict = {
                "action_id": a.get("action_id") or f"a{i}",
                "verb": a.get("verb", ""),
                "args": a.get("args") or {},
                "depends_on": [d for d in (a.get("depends_on") or []) if isinstance(d, str)],
                "rationale": (a.get("rationale") or "")[:300],
            }
            if a.get("produces"):
                item["produces"] = a["produces"]
            hint = a.get("risk_hint")
            if isinstance(hint, str) and hint in _RISK_NAMES:
                item["risk_hint"] = _RISK_NAMES[hint]
            out.append(item)
        return out


@dataclass
class HybridPlanner:
    """LLM first, deterministic templates as the floor. Records which fired."""

    llm: Planner | None = None
    name: str = "hybrid"
    last_strategy: str = ""
    last_fallback_reason: str = ""

    @property
    def model(self) -> str:
        return self.llm.model if self.llm else "rule-based"

    def plan(self, instruction: str, intent: str | None = None,
             slots: Slots | None = None) -> Plan:
        self.last_fallback_reason = ""
        if self.llm is not None:
            try:
                plan = self.llm.plan(instruction, intent, slots)
                self.last_strategy = "llm"
                return plan
            except PlannerError as e:
                self.last_fallback_reason = str(e)

        if intent is None or slots is None:
            raise PlannerError(
                "no LLM available and no intent/slots to build a template plan from"
                + (f" ({self.last_fallback_reason})" if self.last_fallback_reason else "")
            )
        try:
            plan = build_plan(instruction, intent, slots)
        except NoTemplate as e:
            raise PlannerError(
                f"no plan: the deterministic planner has no template for this "
                f"({e})" + (f"; the LLM also failed: {self.last_fallback_reason}"
                            if self.last_fallback_reason else "")
            ) from e
        self.last_strategy = "rule"
        return plan


def _strip_fences(raw: str) -> str:
    """Some models wrap JSON in ```json fences despite the schema. Tolerate it
    here rather than burning a repair attempt on formatting."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _clean_error(e: Exception) -> str:
    """Validator errors go straight back to the model, so keep them short and
    actionable rather than dumping a 40-line pydantic trace into the prompt."""
    if isinstance(e, ValidationError):
        bits = []
        for err in e.errors()[:4]:
            loc = ".".join(str(x) for x in err["loc"])
            bits.append(f"{loc}: {err['msg']}")
        return "; ".join(bits)
    return str(e)[:400]
