"""Planner v0: natural language -> validated Action IR plan (docs/02 §4, L4).

Trust boundaries, enforced structurally:

- The model emits ONLY the actions array. plan_id and the instruction are
  bound by US, so a plan is always traceable to the exact user input.
- The verb enum inside the decoding schema comes from the closed registry —
  the model cannot even *emit* an unregistered verb.
- Whatever comes back still goes through Plan.model_validate and later the
  deterministic scorer. The planner proposes; it never decides (docs/06).
- Repair loop: structural errors are fed back verbatim, max N attempts,
  then fail cleanly (FR-11). No silent retries, no partial plans.
"""

from __future__ import annotations

import json
import platform as _platform
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from maestro import registry
from maestro.ir import Plan
from maestro.llm import LLMClient

MAX_ATTEMPTS = 3


class PlannerError(Exception):
    """Planning failed after all repair attempts — surface, don't guess."""


def _decode_schema() -> dict:
    """JSON schema for constrained decoding. The verb enum IS the registry."""
    return {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "action_id": {"type": "string", "pattern": "^a[0-9]+$"},
                        "verb": {"type": "string", "enum": registry.known_verbs()},
                        "args": {"type": "object"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "produces": {"type": ["string", "null"]},
                        "rationale": {"type": "string"},
                    },
                    # produces/depends_on are REQUIRED (nullable/empty) so the
                    # decoder forces the model to state its dataflow explicitly —
                    # qwen2.5 otherwise omits produces and emits dangling $refs.
                    "required": ["action_id", "verb", "args", "depends_on",
                                 "produces", "rationale"],
                },
            }
        },
        "required": ["actions"],
    }


def _verb_docs() -> str:
    lines = []
    for v in registry.known_verbs():
        spec = registry.get(v)
        if spec.hard_blocked:
            continue  # don't advertise verbs that exist only to be refused
        fields = ", ".join(
            f"{name}: {f.annotation}" for name, f in spec.args_model.model_fields.items()
        )
        lines.append(f"- {v}({fields}) — {spec.description}")
    return "\n".join(lines)


SYSTEM_PROMPT = """You are the planner of MAESTRO, a safe desktop automation assistant.
Convert the user's instruction into a JSON plan: {{"actions": [...]}}.

Available verbs (use ONLY these):
{verbs}

Rules:
- action_id: a1, a2, ... in order.
- Reference an earlier action's output with "$name" — the earlier action MUST
  set "produces": "name" and be listed in this action's depends_on. Every "$name"
  you write requires a matching "produces". Set "produces": null when no later
  action needs the output.
- Use absolute paths under the user's home directory shown in the context.
- Prefer the fewest actions that fully satisfy the instruction. Do not add
  extra cleanup, deletion, or organization the user did not ask for.
- rationale: one short sentence per action, shown to the user.
- If the instruction cannot be satisfied with these verbs, return a single
  fs.stat action on the user's home directory with rationale explaining what
  is missing. Never improvise capabilities.

Example:
Instruction: "move the pdfs from my inbox folder to archive"
{{"actions": [
  {{"action_id": "a1", "verb": "fs.glob",
    "args": {{"root": "/Users/me/maestro_workspace/inbox", "pattern": "*.pdf"}},
    "produces": "pdfs", "rationale": "Find the PDF files in inbox"}},
  {{"action_id": "a2", "verb": "fs.move_batch",
    "args": {{"sources": "$pdfs", "dest_dir": "/Users/me/maestro_workspace/archive"}},
    "depends_on": ["a1"], "rationale": "Move them into archive"}}
]}}"""


@dataclass
class Planner:
    client: LLMClient
    home: Path = field(default_factory=Path.home)

    def plan(self, instruction: str) -> Plan:
        system = SYSTEM_PROMPT.format(verbs=_verb_docs())
        user = self._context_block() + f"\nInstruction: {instruction}"
        schema = _decode_schema()

        errors: list[str] = []
        for _ in range(MAX_ATTEMPTS):
            raw = self.client.chat(system, user, schema=schema)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                errors.append(f"not valid JSON: {e}")
                user = self._repair_prompt(instruction, raw, errors[-1])
                continue
            try:
                plan = Plan.model_validate({
                    "plan_id": f"p_{uuid.uuid4().hex[:8]}",
                    "instruction": instruction,          # bound by us, not the model
                    "actions": self._adapt(data.get("actions", [])),
                })
                # Registry membership check here as well as in the scorer:
                # constrained decoding should make this unreachable, but the
                # planner must not RELY on the decoder (defense in depth).
                for a in plan.actions:
                    registry.get(a.verb)  # raises RegistryError on unknown verb
                return plan
            except (ValidationError, ValueError, registry.RegistryError) as e:
                errors.append(str(e))
                user = self._repair_prompt(instruction, raw, errors[-1])

        raise PlannerError(
            f"no valid plan after {MAX_ATTEMPTS} attempts; last error: {errors[-1]}"
        )

    def _context_block(self) -> str:
        ws = self.home / "maestro_workspace"
        return (
            f"Context:\n- platform: {_platform.system()}\n"
            f"- home: {self.home}\n"
            f"- workspace (preferred for new files): {ws}\n"
            f"- common folders: {self.home}/Downloads, {self.home}/Documents, "
            f"{self.home}/Desktop\n"
        )

    def _repair_prompt(self, instruction: str, bad: str, error: str) -> str:
        return (
            self._context_block()
            + f"\nInstruction: {instruction}\n\nYour previous plan was rejected by the "
            f"validator:\n{error}\n\nPrevious output:\n{bad}\n\nEmit a corrected plan."
        )

    @staticmethod
    def _adapt(actions: list[dict]) -> list[dict]:
        """Map the model's output onto strict Action fields (drop nulls etc.)."""
        out = []
        for a in actions:
            item = {
                "action_id": a.get("action_id", ""),
                "verb": a.get("verb", ""),
                "args": a.get("args", {}),
                "depends_on": a.get("depends_on") or [],
                "rationale": a.get("rationale", ""),
            }
            if a.get("produces"):
                item["produces"] = a["produces"]
            out.append(item)
        return out
