"""Composition and drafting verbs (PRD task category T6).

    draft.email    R2  create an UNSENT email draft
    draft.note     R2  write a note into the workspace
    email.send     R3  registered HARD-BLOCKED — drafting is offered instead

The split between `draft.email` and `email.send` is the whole design argument
of docs/06 §5 in two registry entries: the useful half of the capability is
available and reversible, the irreversible half does not exist as an executable
verb at all. A plan that wants to send mail cannot be written, not merely
refused at runtime.

A draft is written to `<workspace>/drafts/` as an `.eml` file. That is a real,
openable artifact — the user double-clicks it, their mail client opens it
pre-filled, and *they* press send. The human stays in the loop by construction
rather than by policy.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from pydantic import BaseModel, Field

from maestro.config import settings
from maestro.executor.base import Context, EffectManifest, Result, register_executor, resolve
from maestro.ir import Risk
from maestro.registry import VerbSpec, register


class DraftEmailArgs(BaseModel):
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""


class DraftNoteArgs(BaseModel):
    title: str
    body: str = ""


class SendEmailArgs(BaseModel):
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""


register(VerbSpec("draft.email", DraftEmailArgs, Risk.R2, reversible=True, category="draft",
                  description="Create an UNSENT email draft the user can review and send",
                  sensitive_args=("to", "body")))
register(VerbSpec("draft.note", DraftNoteArgs, Risk.R2, reversible=True, category="draft",
                  description="Write a note into the workspace", sensitive_args=("body",)))
# Exists so that refusing it is a tested behavior with a metric (HBR).
register(VerbSpec("email.send", SendEmailArgs, Risk.R3, reversible=False, hard_blocked=True,
                  category="draft", network_write=True,
                  description="Send an email — always refused; use draft.email"))


def _drafts_dir() -> Path:
    d = settings().workspace / "drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(s: str, fallback: str = "draft") -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:60]
    return out or fallback


class DraftEmailExecutor:
    verb = "draft.email"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = DraftEmailArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Draft an email to {', '.join(a.to) or '(no recipient)'} "
                    f"— subject {a.subject!r}",
            files_touched=1,
            bytes_affected=len(a.body.encode()),
            creates=[str(_drafts_dir() / (_slug(a.subject, 'email') + '.eml'))],
            unknowns=["the draft is NOT sent; sending stays a manual action"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = DraftEmailArgs.model_validate(resolve(args, ctx))
        msg = EmailMessage()
        msg["To"] = ", ".join(a.to)
        msg["Subject"] = a.subject
        msg["X-MAESTRO-Draft"] = "true"
        msg["X-MAESTRO-Created"] = datetime.now(timezone.utc).isoformat()
        msg.set_content(a.body)

        target = _unique(_drafts_dir() / (_slug(a.subject, "email") + ".eml"))
        target.write_bytes(bytes(msg))
        return Result(ok=True, output=str(target), files_touched=1,
                      detail=f"draft written to {target.name} (not sent)",
                      undo_data={"draft": str(target)})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data or {}
        if d.get("draft"):
            Path(d["draft"]).unlink(missing_ok=True)


class DraftNoteExecutor:
    verb = "draft.note"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = DraftNoteArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Write note {a.title!r} ({len(a.body)} chars)",
            files_touched=1,
            bytes_affected=len(a.body.encode()),
            creates=[str(_drafts_dir() / (_slug(a.title, "note") + ".md"))],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = DraftNoteArgs.model_validate(resolve(args, ctx))
        target = _unique(_drafts_dir() / (_slug(a.title, "note") + ".md"))
        target.write_text(f"# {a.title}\n\n{a.body}\n", encoding="utf-8")
        return Result(ok=True, output=str(target), files_touched=1,
                      detail=f"note written to {target.name}",
                      undo_data={"note": str(target)})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data or {}
        if d.get("note"):
            Path(d["note"]).unlink(missing_ok=True)


def _unique(target: Path) -> Path:
    if not target.exists():
        return target
    for i in range(1, 1000):
        cand = target.with_name(f"{target.stem} ({i}){target.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(target)


for _ex in (DraftEmailExecutor(), DraftNoteExecutor()):
    register_executor(_ex)
