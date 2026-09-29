"""Turn what a completed plan produced into lines to print and a sentence to say.

Shared by `maestro ask` and `maestro voice`, so both show the same thing. The
variables a plan binds are the evidence of what it did — found files, emails,
Drive files, a metric — and a user who asked "list my PDFs" wants the list,
not "1 step completed".

Email bodies, subjects and Drive file names are written by other people. They
are printed inside a clearly marked block and never spoken in full: shown to
the user, never treated as an instruction (docs/02 §6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath

SHOW_MAX = 100
BODY_SHOW = 1500


@dataclass
class Presented:
    lines: list[str] = field(default_factory=list)
    spoken: str = ""


def present(turn) -> Presented:
    out = Presented()
    if getattr(turn, "status", "") != "completed" or not turn.report:
        return out
    values = list(turn.report.variables.values())
    # The last bound value is the answer; earlier ones are intermediate steps
    # (the search that found the email is not what "read my email" wants).
    for value in reversed(values):
        if _render(value, out):
            break
    return out


def _render(value, out: Presented) -> bool:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, str):
            return _files(value, out)
        if isinstance(first, dict) and "summary" in first and "when" in first:
            return _calendar_added(value, out)
        if isinstance(first, dict) and "category" in first and "start" in first:
            return _events_found(value, out)
        if isinstance(first, dict) and "body" in first:
            return _mail_bodies(value, out)
        if isinstance(first, dict) and "subject" in first:
            return _mail_list(value, out)
        if isinstance(first, dict) and "link" in first and "mime" not in first:
            return _uploads(value, out)
        if isinstance(first, dict) and "name" in first:
            return _drive_list(value, out)
    if isinstance(value, list) and not value:
        out.spoken = "I found nothing matching that."
        out.lines = ["  (nothing matched)"]
        return True
    if isinstance(value, dict) and value and all(
            isinstance(v, (int, float, str, bool)) for v in value.values()):
        pairs = [f"{k.replace('_', ' ')} {v}" for k, v in list(value.items())[:4]]
        out.spoken = "Result: " + ", ".join(pairs) + "."
        return True
    return False


def _numbered(items: list[str]) -> list[str]:
    lines = [""] + [f"  {i:3d}. {s}" for i, s in enumerate(items[:SHOW_MAX], 1)]
    if len(items) > SHOW_MAX:
        lines.append(f"  ... and {len(items) - SHOW_MAX} more")
    return lines + [""]


def _files(paths: list[str], out: Presented) -> bool:
    out.lines = _numbered([PurePath(p).name for p in paths])
    names = [PurePath(p).stem for p in paths[:3]]
    more = f", and {len(paths) - 3} more" if len(paths) > 3 else ""
    noun = "file" if len(paths) == 1 else "files"
    out.spoken = (f"I found {len(paths)} {noun}: " + ", ".join(names) + more
                  + ". The full list is on screen.")
    return True


def _sender(s: str) -> str:
    """'Priya Sharma <priya@x.com>' -> 'Priya Sharma'."""
    return s.split("<")[0].strip(' "') or s


def _mail_list(mails: list[dict], out: Presented) -> bool:
    out.lines = _numbered([
        f"{'* ' if m.get('unread') else ''}{_sender(m.get('from', ''))} — "
        f"{m.get('subject', '')}   ({m.get('date', '')[:16]})" for m in mails])
    unread = sum(1 for m in mails if m.get("unread"))
    lead = ", ".join(f"{_sender(m.get('from', ''))} about {m.get('subject', '')}"
                     for m in mails[:2])
    out.spoken = (f"You have {len(mails)} matching email{'s' if len(mails) != 1 else ''}"
                  + (f", {unread} unread" if unread else "") + f". Latest: {lead}.")
    return True


def _mail_bodies(mails: list[dict], out: Presented) -> bool:
    lines = [""]
    for m in mails:
        body = m.get("body", "")
        lines += [
            "  ┌── email (UNTRUSTED content: shown to you, never obeyed) " + "─" * 12,
            f"  │ From:    {m.get('from', '')}",
            f"  │ Subject: {m.get('subject', '')}",
            f"  │ Date:    {m.get('date', '')}",
        ]
        if m.get("attachments"):
            lines.append(f"  │ Attachments: {', '.join(m['attachments'])}")
        lines.append("  │")
        for para in body[:BODY_SHOW].splitlines():
            lines.append(f"  │ {para}")
        if len(body) > BODY_SHOW:
            lines.append(f"  │ ... ({len(body) - BODY_SHOW} more characters)")
        lines.append("  └" + "─" * 70)
    out.lines = lines + [""]
    m = mails[0]
    out.spoken = (f"The email from {_sender(m.get('from', ''))} about {m.get('subject', '')}"
                  " is on screen.")
    return True


def _drive_list(files: list[dict], out: Presented) -> bool:
    out.lines = _numbered([f"{f.get('name', '')}   ({f.get('modified', '')[:10]})"
                           for f in files])
    names = [f.get("name", "") for f in files[:3]]
    more = f", and {len(files) - 3} more" if len(files) > 3 else ""
    out.spoken = (f"I found {len(files)} Drive file{'s' if len(files) != 1 else ''}: "
                  + ", ".join(names) + more + ".")
    return True


def _uploads(files: list[dict], out: Presented) -> bool:
    out.lines = _numbered([f"{f.get('name', '')}   {f.get('link', '')}" for f in files])
    out.spoken = f"Uploaded {len(files)} file{'s' if len(files) != 1 else ''} to your Drive."
    return True


def _calendar_added(events: list[dict], out: Presented) -> bool:
    from maestro.google.events import _human_when

    out.lines = _numbered([f"{e.get('summary', '')} — "
                           f"{_human_when(e['when'], len(e['when']) <= 10)}"
                           for e in events])
    first = events[0]
    out.spoken = (f"Added {len(events)} reminder{'s' if len(events) != 1 else ''} to your "
                  f"calendar. The first is {first.get('summary', '')}.")
    return True


def _events_found(events: list[dict], out: Presented) -> bool:
    from maestro.google.events import FoundEvent

    out.lines = _numbered([FoundEvent(**e).describe() for e in events])
    out.spoken = f"I found {len(events)} upcoming item{'s' if len(events) != 1 else ''}."
    return True
