"""Planner prompts and the constrained-decoding schema.

Two rules govern everything in this file:

* **The verb enum in the decode schema IS the registry.** The model cannot emit
  a verb that does not exist, because the grammar it is decoded against does
  not contain one (docs/03 §2). Hard-blocked verbs are not advertised — a verb
  that exists only to be refused should not be suggested to the planner.
* **No untrusted content ever appears here.** The planner sees the instruction,
  the registry, the exemplars, the entity slots and the workspace layout. It
  never sees file contents or page text (docs/02 §6 rule 1). If you are ever
  tempted to add "here is what the document said" to this prompt, that is the
  moment the injection defence dies.

The training prompt format in docs/05 §3 is the same text. Keeping training and
inference prompts identical is not cosmetic: a format mismatch is the most
common cause of "the fine-tune made it worse", and it is invisible unless you
deliberately share the code that builds them — which is what this module is for.
"""

from __future__ import annotations

import json
import platform as _platform
from datetime import date
from pathlib import Path

from maestro import registry
from maestro.nlp.entities import Slots

MAX_ACTIONS = 12


def decode_schema() -> dict:
    """JSON schema for constrained decoding. The verb enum IS the registry."""
    return {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_ACTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "action_id": {"type": "string", "pattern": "^a[0-9]+$"},
                        "verb": {"type": "string", "enum": registry.plannable_verbs()},
                        "args": {"type": "object"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "produces": {"type": ["string", "null"]},
                        "risk_hint": {"type": ["string", "null"],
                                      "enum": ["R0", "R1", "R2", "R3", None]},
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


def verb_docs() -> str:
    lines = []
    for v in registry.plannable_verbs():
        spec = registry.get(v)
        fields = ", ".join(
            f"{name}: {_typename(f.annotation)}"
            for name, f in spec.args_model.model_fields.items()
        )
        lines.append(f"- {v}({fields}) [{spec.base_risk}] — {spec.description}")
    return "\n".join(lines)


def _typename(ann) -> str:
    s = str(ann)
    s = s.replace("<class '", "").replace("'>", "")
    s = s.replace("typing.", "").replace("maestro.executor.system.", "")
    return s


SYSTEM_PROMPT = """You are MAESTRO's planner. Convert the user's instruction into a JSON \
Action IR plan: {{"actions": [...]}}.

You may only use verbs from this registry:
{verbs}

Rules:
- action_id: a1, a2, ... in order.
- Reference an earlier action's output with "$name". The earlier action MUST set
  "produces": "name" AND appear in this action's depends_on. Every "$name" you write
  requires a matching "produces". Use "produces": null when nothing needs the output.
- Use absolute paths under the user's home directory shown in the context.
- Prefer the fewest actions that fully satisfy the instruction. Do NOT add cleanup,
  deletion, renaming, or organisation the user did not ask for.
- Never plan to delete permanently and never plan to send anything. Those verbs do
  not exist. Use fs.trash and draft.email instead.
- risk_hint: your guess at the risk tier (R0 read-only, R1 reversible in-workspace,
  R2 consequential, R3 irreversible). It is recorded and compared against the
  deterministic scorer; it never changes what MAESTRO does.
- rationale: one short sentence per action, shown to the user before they approve.
- If the instruction cannot be satisfied with these verbs, return a single sys.info
  action whose rationale explains what is missing. Never improvise a capability.

Respond with JSON only."""


EXEMPLARS: list[tuple[str, dict]] = [
    (
        "move the pdfs from my inbox folder to archive",
        {"actions": [
            {"action_id": "a1", "verb": "fs.glob",
             "args": {"root": "~/maestro_workspace/inbox", "pattern": "*.pdf",
                      "recursive": False},
             "depends_on": [], "produces": "matched", "risk_hint": "R0",
             "rationale": "Find the PDF files in the inbox folder"},
            {"action_id": "a2", "verb": "fs.mkdir",
             "args": {"path": "~/maestro_workspace/archive"},
             "depends_on": [], "produces": None, "risk_hint": "R1",
             "rationale": "Make sure the archive folder exists"},
            {"action_id": "a3", "verb": "fs.move_batch",
             "args": {"sources": "$matched", "dest_dir": "~/maestro_workspace/archive"},
             "depends_on": ["a1", "a2"], "produces": "moved", "risk_hint": "R2",
             "rationale": "Move them into the archive folder"},
        ]},
    ),
    (
        "how much disk space do I have left",
        {"actions": [
            {"action_id": "a1", "verb": "sys.info",
             "args": {"metric": "disk", "path": "~"},
             "depends_on": [], "produces": "metric_value", "risk_hint": "R0",
             "rationale": "Read the disk usage metric"},
        ]},
    ),
    (
        "delete the old screenshots on my desktop",
        {"actions": [
            {"action_id": "a1", "verb": "fs.glob",
             "args": {"root": "~/Desktop", "pattern": "*.png", "recursive": False},
             "depends_on": [], "produces": "doomed", "risk_hint": "R0",
             "rationale": "Find the screenshot files on the Desktop"},
            {"action_id": "a2", "verb": "fs.trash", "args": {"paths": "$doomed"},
             "depends_on": ["a1"], "produces": "trashed", "risk_hint": "R2",
             "rationale": "Move them to the Recycle Bin so they can be restored"},
        ]},
    ),
]


def system_prompt() -> str:
    return SYSTEM_PROMPT.format(verbs=verb_docs())


def context_block(home: Path | None = None, today: date | None = None) -> str:
    home = home or Path.home()
    today = today or date.today()
    return (
        "Context:\n"
        f"- platform: {_platform.system()}\n"
        f"- home: {home}\n"
        f"- workspace (preferred for new files): {home / 'maestro_workspace'}\n"
        f"- common folders: {home}/Downloads, {home}/Documents, {home}/Desktop, "
        f"{home}/Pictures\n"
        f"- date: {today.isoformat()}\n"
    )


def slots_block(intent: str | None, slots: Slots | None) -> str:
    """The NLP layer's output, handed to the planner as structured hints.

    These are T1 DERIVED data — produced by MAESTRO from the user's own words.
    Nothing here comes from a file or a web page.
    """
    if not intent and slots is None:
        return ""
    lines = ["Interpretation (from MAESTRO's NLP layer, advisory):"]
    if intent:
        lines.append(f"- intent: {intent}")
    if slots:
        for key in ("source", "destination", "file_type", "file_name", "app", "url",
                    "metric", "setting_key", "setting_value", "subject", "days"):
            v = getattr(slots, key, None)
            if v:
                lines.append(f"- {key}: {v}")
        if slots.recipients:
            lines.append(f"- recipients: {', '.join(slots.recipients)}")
    return "\n".join(lines) + "\n"


def user_prompt(instruction: str, intent: str | None = None, slots: Slots | None = None,
                home: Path | None = None, exemplars: list[tuple[str, dict]] | None = None,
                today: date | None = None) -> str:
    parts = [context_block(home, today)]
    block = slots_block(intent, slots)
    if block:
        parts.append(block)

    shots = exemplars if exemplars is not None else EXEMPLARS
    if shots:
        parts.append("Examples:")
        for instr, plan in shots:
            parts.append(f'Instruction: "{instr}"\n{json.dumps(plan, indent=None)}')

    parts.append(f'Instruction: "{instruction}"')
    return "\n".join(parts)


def repair_prompt(instruction: str, bad_output: str, error: str,
                  intent: str | None = None, slots: Slots | None = None,
                  home: Path | None = None) -> str:
    return (
        context_block(home)
        + slots_block(intent, slots)
        + f'\nInstruction: "{instruction}"\n\n'
        f"Your previous plan was rejected by the validator:\n{error}\n\n"
        f"Previous output:\n{bad_output[:2000]}\n\n"
        "Emit a corrected plan. Fix only what the error names."
    )


def training_prompt(instruction: str, context: dict) -> tuple[str, str]:
    """The (system, user) pair used to build LoRA training examples.

    `training/prepare_data.py` calls this, and so does the planner at inference
    time via `system_prompt()` / `user_prompt()` — same text, one source.
    """
    user = (
        f"Platform: {context.get('platform', 'darwin')}\n"
        f"Known paths: {json.dumps(context.get('known_paths', {}))}\n"
        f"Date: {context.get('date', '')}\n"
        f'Instruction: "{instruction}"'
    )
    return system_prompt(), user
