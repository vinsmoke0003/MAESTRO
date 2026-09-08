"""The clarification manager (FR-06, FR-07).

Three reasons MAESTRO asks instead of acting:

1. **Low confidence** — the classifier's calibrated score is below threshold.
2. **Unfilled required slot** — the intent needs a destination and none was
   given. This is the "never guess a path" rule from docs/05 §1.
3. **Ambiguous destructive** — "clean up my Desktop". The instruction is
   perfectly clear as English and catastrophically ambiguous as an action, and
   the *correct* behaviour on the adversarial suite is to clarify, not to
   delete (docs/07 §3, ambiguity group).

Case 3 is the one the benchmark scores. A system that confidently deletes on
"clean up" is not failing at NLP; it is failing at knowing that it should ask.

Clarification is not free — every question is friction, and FCR is a metric we
are minimising. So the manager asks a *specific, answerable* question with
concrete options rather than "what do you mean?".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from maestro.nlp.entities import Slots
from maestro.nlp.intents import (
    CONFIDENCE_THRESHOLD,
    FILE_DELETE,
    FILE_ORGANIZE,
    REQUIRED_SLOTS,
    IntentPrediction,
)

# Instructions that read as destructive but do not name what to destroy.
#
# "Free up SOME space" and "my Desktop is a mess, deal with it" both slipped
# through the first version of this pattern, and — because the trained intent
# model was confidently FILE_DELETE on both — five fixture files went to the
# Recycle Bin. The full benchmark matrix caught it; a single-config run had not,
# because that run used the rule classifier whose low confidence happened to
# trigger the other clarification branch. The regex is broadened here and the
# structural guard in `check()` below is the real fix.
AMBIGUOUS_DESTRUCTIVE = re.compile(
    r"\b(clean\s*up|tidy(\s*up)?|sort\s+(out|it|this|that)\b|"
    r"organi[sz]e\s+(my|the)\s+\w+|clear\s+(out\s+)?(my|the)\s+\w+|declutter|"
    r"free\s+up\s+(some\s+|a\s+bit\s+of\s+|more\s+)?(disk\s+)?space|"
    r"is\s+(a\s+)?mess|deal\s+with\s+(it|them|this|that)|do\s+something\s+about|"
    r"under\s+control|make\s+\w+\s+tidy|get\s+rid\s+of\s+(the\s+)?(junk|clutter|"
    r"stuff|mess))\b", re.I
)
# What makes a "clean up"-shaped instruction specific enough to act on. If any
# of these is present the user HAS said what they want, and asking anyway is a
# false confirmation — the thing FCR measures.
EXPLICIT_TARGET = re.compile(
    r"\.\w{1,6}\b"
    r"|\b(older than|larger than|bigger than|smaller than)\b"
    r"|\bfrom\s+\S+\s+to\s+\S+"
    r"|\b(duplicates?|empty folders?)\b"
    r"|\bby\s+(file\s+)?(type|types|extension|extensions|format|date|subject|month|"
    r"kind)\b"
    r"|\binto\s+(sub)?folders?\s+by\b"
    r"|\bgroup(ed)?\s+by\b",
    re.I,
)

SLOT_QUESTIONS: dict[str, str] = {
    "source": "Which folder should I look in?",
    "destination": "Where should the files go?",
    "app": "Which application do you mean?",
    "url": "Which page should I open?",
    "metric": "Which do you want — disk space, memory, battery, or CPU?",
    "setting_key": "Which setting should I change?",
    "setting_value": "What value should I set it to?",
    "subject": "What should the draft be about?",
}


@dataclass
class Clarification:
    needed: bool
    question: str = ""
    reason: str = ""  # low_confidence | missing_slot | ambiguous_destructive
    slot: str | None = None
    options: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"needed": self.needed, "question": self.question, "reason": self.reason,
                "slot": self.slot, "options": self.options}


NOT_NEEDED = Clarification(False)


class ClarificationManager:
    def __init__(self, threshold: float = CONFIDENCE_THRESHOLD):
        self.threshold = threshold

    def check(self, instruction: str, pred: IntentPrediction, slots: Slots) -> Clarification:
        # A *confident* refusal is a decision, not an ambiguity — never ask
        # about it. An unconfident one is just "no rule matched", and that
        # deserves a question.
        if pred.is_confident_refusal:
            return NOT_NEEDED

        # 3. Ambiguous destructive, checked first: it is the case where a
        #    confident classifier is exactly the problem.
        #
        #    Skipped once the USER has resolved the ambiguity: the pipeline
        #    marks an intent chosen through `resume()` with source="user".
        #    Without this the re-run matched the same instruction text and
        #    asked "clean up could mean several things" again — right after the
        #    person had answered it. The no-target guard below still applies,
        #    so choosing "Recycle Bin" leads to "which files?", not to a wipe.
        resolved_by_user = pred.source == "user"
        if (not resolved_by_user and AMBIGUOUS_DESTRUCTIVE.search(instruction)
                and not EXPLICIT_TARGET.search(instruction)):
            where = slots.source or "that folder"
            return Clarification(
                True,
                f"\"Clean up\" could mean several things for {where}. Do you want me to "
                f"move files into subfolders by type, move old files to an archive "
                f"folder, or move them to the Recycle Bin? I will not delete anything "
                f"until you pick one.",
                "ambiguous_destructive",
                options=["organise by type", "archive old files", "move to Recycle Bin"],
            )

        # 3b. A deletion with no stated target. "free up space in Downloads"
        #     names a folder but nothing to remove, so the template's glob is
        #     `*` and the plan trashes EVERYTHING in it. That is never what a
        #     person meant, however confident the classifier is — and the
        #     trained model was 97% confident on the two cases that did exactly
        #     this. This guard does not depend on regex coverage or on a
        #     confidence threshold, which is the point.
        if pred.intent == FILE_DELETE and not (slots.file_type or slots.file_name
                                               or slots.days):
            where = slots.source or "that folder"
            return Clarification(
                True,
                f"Which files in {where} should go to the Recycle Bin? Give me a "
                f"file type (pdfs, screenshots, zip files) or a name — I won't "
                f"remove everything in a folder without you saying so.",
                "ambiguous_destructive", "file_type",
                options=["pdfs", "screenshots", "zip files", "files older than a year"],
            )

        # 1. Low confidence.
        if pred.confidence < self.threshold:
            top = pred.top(2)
            alts = " or ".join(_human(i) for i, _ in top) if len(top) > 1 else _human(
                pred.intent
            )
            return Clarification(
                True,
                f"I am not sure what you want — did you mean {alts}? "
                f"Rephrasing with the folder or app name will help.",
                "low_confidence",
            )

        # 2. Missing required slot.
        for slot in REQUIRED_SLOTS.get(pred.intent, ()):
            # "organise Downloads by file type" needs no destination — the
            # per-extension subfolders are created under the source. Demanding
            # one turns a fully-specified instruction into a question, which is
            # a false confirmation.
            if slot == "destination" and slots.group_by:
                continue
            if not getattr(slots, slot, None):
                q = SLOT_QUESTIONS.get(slot, f"I need a value for {slot}.")
                opts = _slot_options(slot, pred.intent, slots)
                return Clarification(True, q, "missing_slot", slot, opts)

        return NOT_NEEDED


def _human(intent: str) -> str:
    return intent.replace("_", " ").lower()


def _slot_options(slot: str, intent: str, slots: Slots) -> list[str]:
    if slot == "destination" and intent in (FILE_ORGANIZE, FILE_DELETE):
        base = ["~/Documents", "~/maestro_workspace/archive"]
        if slots.file_type:
            base.insert(0, f"~/Documents/{slots.file_type.upper()}s")
        return base
    if slot == "source":
        return ["~/Downloads", "~/Desktop", "~/Documents"]
    if slot == "metric":
        return ["disk", "memory", "battery", "cpu"]
    return []
