"""Google verbs: Gmail and Drive, inside the same closed registry.

    gmail.search     R0  list messages matching a Gmail query
    gmail.read       R0  read up to 5 messages (content is UNTRUSTED)
    gmail.draft      R2  create an UNSENT draft in your Gmail Drafts folder
    drive.search     R0  list Drive files by name / kind
    drive.download   R1  copy Drive files into a local folder (undo: removes them)
    drive.upload     R2  upload local files to your Drive (undo: trashes them)
    mail.find_events R0  find tests, tickets, deadlines in emails (no network)
    calendar.add_events R2  add those as reminders to YOUR Google Calendar
    drive.share      --  HARD-BLOCKED: making data visible to others
    drive.delete     --  HARD-BLOCKED: removing data from your Drive

There is still no `email.send` executor, and none is added here: MAESTRO writes
the draft and you press Send in Gmail. `drive.share` and `drive.delete` exist
only so that refusing them is a tested behaviour, exactly like `email.send`
(docs/06 §5) — "share my Drive folder with x@y.com" produces a plan that the
scorer blocks, not a plan that happens to fail.

Why these tiers: reading changes nothing (R0). Downloading writes into your
own folder and is undone by removing the copies (R1). A draft and an upload
both put data on Google's servers, so they are network writes and need your
spoken or clicked approval (R2), even though both can be undone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maestro.executor.base import Context, EffectManifest, Result, register_executor, resolve
from maestro.google.auth import GoogleNotConnected
from maestro.ir import Risk
from maestro.registry import VerbSpec, register

MAX_READ = 15
MAX_EVENTS = 12
MAX_TRANSFER = 20


class MailSearchArgs(BaseModel):
    query: str = "in:inbox"
    limit: int = Field(10, ge=1, le=50)


class MailReadArgs(BaseModel):
    messages: list[Any] = Field(default_factory=list)   # ids, or results of gmail.search
    limit: int = Field(1, ge=1, le=MAX_READ)


class MailDraftArgs(BaseModel):
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""


class DriveSearchArgs(BaseModel):
    name: str = ""
    kind: str | None = None          # "pdf", "folder", "document", ...
    limit: int = Field(20, ge=1, le=50)


class DriveDownloadArgs(BaseModel):
    files: list[Any] = Field(default_factory=list)       # ids, or results of drive.search
    dest_dir: str


class DriveUploadArgs(BaseModel):
    paths: list[str] = Field(default_factory=list)
    folder_id: str | None = None


class FindEventsArgs(BaseModel):
    mails: list[Any] = Field(default_factory=list)


class CalendarEventArgs(BaseModel):
    """One event, validated field by field: it came out of an email."""

    title: str = Field(max_length=120)
    start: str
    end: str | None = None
    all_day: bool = False
    category: str = "Event"
    location: str = Field("", max_length=200)
    source: str = Field("", max_length=100)
    source_from: str = Field("", max_length=200)
    source_subject: str = Field("", max_length=300)
    evidence: str = Field("", max_length=300)


class AddEventsArgs(BaseModel):
    events: list[Any] = Field(default_factory=list)
    reminders: list[int] = Field(default_factory=lambda: [1440, 60], max_length=5)


class DriveShareArgs(BaseModel):
    file: str = ""
    with_: list[str] = Field(default_factory=list, alias="with")


class DriveDeleteArgs(BaseModel):
    file: str = ""


register(VerbSpec("gmail.search", MailSearchArgs, Risk.R0, reversible=True, category="google",
                  description="List Gmail messages matching a Gmail search query"))
register(VerbSpec("gmail.read", MailReadArgs, Risk.R0, reversible=True, category="google",
                  description="Read up to 5 Gmail messages; their content is untrusted"))
register(VerbSpec("gmail.draft", MailDraftArgs, Risk.R2, reversible=True, category="google",
                  network_write=True, sensitive_args=("to", "subject", "body"),
                  description="Create an UNSENT draft in Gmail; the user sends it"))
register(VerbSpec("drive.search", DriveSearchArgs, Risk.R0, reversible=True,
                  category="google", description="List Google Drive files by name or kind"))
register(VerbSpec("drive.download", DriveDownloadArgs, Risk.R1, reversible=True,
                  category="google", path_args=("dest_dir",), sensitive_args=("dest_dir",),
                  description="Download Drive files into a local folder"))
register(VerbSpec("drive.upload", DriveUploadArgs, Risk.R2, reversible=True, category="google",
                  network_write=True, path_args=("paths",), sensitive_args=("paths",),
                  description="Upload local files to the user's Google Drive"))
register(VerbSpec("mail.find_events", FindEventsArgs, Risk.R0, reversible=True,
                  category="google",
                  description="Find dated tests, tickets and deadlines in read emails"))
register(VerbSpec("calendar.add_events", AddEventsArgs, Risk.R2, reversible=True,
                  category="google", network_write=True, sensitive_args=("events",),
                  description="Add reminders to the user's own Google Calendar (no guests)"))
# Exist so that refusing them is a tested behaviour with a metric (HBR).
register(VerbSpec("drive.share", DriveShareArgs, Risk.R3, reversible=False, hard_blocked=True,
                  category="google", network_write=True,
                  description="Share a Drive file with others — always refused"))
register(VerbSpec("drive.delete", DriveDeleteArgs, Risk.R3, reversible=False,
                  hard_blocked=True, category="google",
                  description="Delete a Drive file — always refused"))


def _google():
    """The Google API adapter (or the fake one tests install)."""
    from maestro.google.api import api

    return api()


def _ids(items: list[Any], limit: int) -> list[str]:
    """Pull message or file ids out of a list that may hold ids or search results, keeping at most
    `limit`.
    """
    out = []
    for x in items:
        if isinstance(x, dict) and x.get("id"):
            out.append(str(x["id"]))
        elif isinstance(x, str) and x:
            out.append(x)
    return out[:limit]


def _not_connected(e: Exception) -> Result:
    """Turn 'Google is not connected' into a failed step whose message says how to connect."""
    return Result(ok=False, detail=str(e))


# --------------------------------------------------------------------------- #
# Gmail
# --------------------------------------------------------------------------- #


class MailSearchExecutor:
    verb = "gmail.search"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        """Preview: the Gmail search that will be run (read only)."""
        a = MailSearchArgs.model_validate(resolve(args, ctx))
        return EffectManifest(summary=f"Search Gmail for {a.query!r} (up to {a.limit})",
                              external=["Gmail (read only)"])

    def execute(self, args: dict, ctx: Context) -> Result:
        """Search Gmail and return the matching messages' sender, subject, date and snippet."""
        a = MailSearchArgs.model_validate(resolve(args, ctx))
        try:
            found = _google().search_mail(a.query, a.limit)
        except GoogleNotConnected as e:
            return _not_connected(e)
        return Result(ok=True, output=found, untrusted=True,
                      detail=f"{len(found)} message(s) match {a.query!r}")

    def undo(self, result: Result, ctx: Context) -> None:
        """Reading changes nothing, so there is nothing to undo."""
        return None


class MailReadExecutor:
    verb = "gmail.read"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        """Preview: how many emails will be read, noting their content is shown but never obeyed.
        """
        a = MailReadArgs.model_validate(resolve(args, ctx))
        return EffectManifest(summary=f"Read up to {a.limit} email(s)",
                              external=["Gmail (read only)"],
                              unknowns=["email content is shown to you, never obeyed"])

    def execute(self, args: dict, ctx: Context) -> Result:
        """Read the full text of the found emails. The content is untrusted."""
        a = MailReadArgs.model_validate(resolve(args, ctx))
        ids = _ids(a.messages, a.limit)
        if not ids:
            return Result(ok=True, output=[], detail="no matching email to read")
        try:
            mails = [_google().read_mail(i) for i in ids]
        except GoogleNotConnected as e:
            return _not_connected(e)
        return Result(ok=True, output=mails, untrusted=True,
                      detail=f"read {len(mails)} email(s)")

    def undo(self, result: Result, ctx: Context) -> None:
        """Reading changes nothing, so there is nothing to undo."""
        return None


class MailDraftExecutor:
    verb = "gmail.draft"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        """Preview: the draft's recipients and subject, noting it will not be sent."""
        a = MailDraftArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Create a Gmail DRAFT to {', '.join(a.to) or '(no recipient)'} "
                    f"— subject {a.subject!r}",
            bytes_affected=len(a.body.encode()),
            external=["Gmail Drafts folder (NOT sent)"],
            unknowns=["the draft is NOT sent; you send it yourself in Gmail"])

    def execute(self, args: dict, ctx: Context) -> Result:
        """Save the email in the Gmail Drafts folder. It is never sent; the user sends it in Gmail.
        """
        a = MailDraftArgs.model_validate(resolve(args, ctx))
        try:
            d = _google().create_draft(a.to, a.subject, a.body)
        except GoogleNotConnected as e:
            return _not_connected(e)
        return Result(ok=True, output=d["draft_id"], undo_data=d,
                      detail="draft saved in Gmail Drafts (not sent)")

    def undo(self, result: Result, ctx: Context) -> None:
        """Delete the draft that was created."""
        d = result.undo_data or {}
        if d.get("draft_id"):
            _google().delete_draft(d["draft_id"])


# --------------------------------------------------------------------------- #
# Drive
# --------------------------------------------------------------------------- #


class DriveSearchExecutor:
    verb = "drive.search"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        """Preview: what will be searched for in Google Drive (read only)."""
        a = DriveSearchArgs.model_validate(resolve(args, ctx))
        what = " ".join(x for x in (a.kind or "", f"named like {a.name!r}" if a.name else "")
                        if x) or "recent files"
        return EffectManifest(summary=f"Search Google Drive for {what}",
                              external=["Google Drive (read only)"])

    def execute(self, args: dict, ctx: Context) -> Result:
        """Search Google Drive by name or kind and return the matching files."""
        a = DriveSearchArgs.model_validate(resolve(args, ctx))
        try:
            files = _google().search_drive(a.name, a.limit, a.kind)
        except GoogleNotConnected as e:
            return _not_connected(e)
        return Result(ok=True, output=files, untrusted=True,
                      detail=f"{len(files)} Drive file(s) found")

    def undo(self, result: Result, ctx: Context) -> None:
        """Reading changes nothing, so there is nothing to undo."""
        return None


class DriveDownloadExecutor:
    verb = "drive.download"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        """Preview: how many Drive files will be copied into which local folder."""
        try:
            a = DriveDownloadArgs.model_validate(resolve(args, ctx))
            n = len(_ids(a.files, MAX_TRANSFER))
            where = a.dest_dir
        except Exception:
            n, where = 0, str(args.get("dest_dir"))
        return EffectManifest(
            summary=f"Download {n or 'the matching'} Drive file(s) into {where}",
            files_touched=n, external=["Google Drive (read only)"],
            unknowns=[] if n else ["which files depends on the Drive search"])

    def execute(self, args: dict, ctx: Context) -> Result:
        """Download the found Drive files into a local folder (at most MAX_TRANSFER at once). File
        names are made safe first.
        """
        a = DriveDownloadArgs.model_validate(resolve(args, ctx))
        ids = _ids(a.files, MAX_TRANSFER + 1)
        if len(ids) > MAX_TRANSFER:
            return Result(ok=False, detail=f"refusing to download more than {MAX_TRANSFER} "
                                           "files at once; narrow the search")
        dest = Path(a.dest_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        try:
            for i in ids:
                saved.append(str(_google().download(i, dest)))
        except GoogleNotConnected as e:
            return _not_connected(e)
        except Exception as e:
            return Result(ok=False, detail=f"download failed: {e}", undo_data={"saved": saved},
                          files_touched=len(saved))
        return Result(ok=True, output=saved, files_touched=len(saved),
                      undo_data={"saved": saved},
                      detail=f"downloaded {len(saved)} file(s) into {dest}")

    def undo(self, result: Result, ctx: Context) -> None:
        """Send the downloaded copies to the Trash."""
        from send2trash import send2trash

        for p in (result.undo_data or {}).get("saved", []):
            if Path(p).exists():
                send2trash(p)


class DriveUploadExecutor:
    verb = "drive.upload"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        """Preview: how many local files will be uploaded to the user's own Drive, and their size.
        """
        try:
            a = DriveUploadArgs.model_validate(resolve(args, ctx))
            paths = [Path(p).expanduser() for p in a.paths]
        except Exception:
            paths = []
        size = sum(p.stat().st_size for p in paths if p.is_file())
        return EffectManifest(
            summary=f"Upload {len(paths) or 'the matching'} file(s) to your Google Drive",
            files_touched=len(paths), bytes_affected=size,
            external=["Google Drive (your account; not shared with anyone)"],
            unknowns=[] if paths else ["which files depends on the earlier step"])

    def execute(self, args: dict, ctx: Context) -> Result:
        """Upload local files to the user's Drive (not shared with anyone), at most MAX_TRANSFER at
        once.
        """
        a = DriveUploadArgs.model_validate(resolve(args, ctx))
        paths = [Path(p).expanduser() for p in a.paths]
        if len(paths) > MAX_TRANSFER:
            return Result(ok=False, detail=f"refusing to upload more than {MAX_TRANSFER} "
                                           "files at once")
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            return Result(ok=False, detail=f"not a file: {missing[0]}")
        uploaded: list[dict] = []
        try:
            for p in paths:
                uploaded.append(_google().upload(p, a.folder_id))
        except GoogleNotConnected as e:
            return _not_connected(e)
        except Exception as e:
            return Result(ok=False, detail=f"upload failed: {e}",
                          undo_data={"uploaded": uploaded}, files_touched=len(uploaded))
        return Result(ok=True, output=uploaded, files_touched=len(uploaded),
                      undo_data={"uploaded": uploaded},
                      detail=f"uploaded {len(uploaded)} file(s) to Google Drive")

    def undo(self, result: Result, ctx: Context) -> None:
        """Move the files this run uploaded to the Drive trash. Only MAESTRO's own uploads can be
        touched.
        """
        for f in (result.undo_data or {}).get("uploaded", []):
            _google().trash_own_upload(f["id"])


# --------------------------------------------------------------------------- #
# email -> calendar
# --------------------------------------------------------------------------- #


class FindEventsExecutor:
    """Pure function over emails already read. No network, no side effect."""

    verb = "mail.find_events"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        """Preview: how many emails will be searched for tests, tickets and deadlines."""
        try:
            n = len(FindEventsArgs.model_validate(resolve(args, ctx)).mails)
        except Exception:
            n = 0
        return EffectManifest(summary=f"Look for tests, tickets and deadlines in "
                                      f"{n or 'the'} email(s)")

    def execute(self, args: dict, ctx: Context) -> Result:
        """Find dated events (exams, flights, trains, deadlines...) in the emails already read.
        Pure computation, no network.
        """
        from maestro.google.events import find_events

        a = FindEventsArgs.model_validate(resolve(args, ctx))
        mails = [m for m in a.mails if isinstance(m, dict)]
        found = [e.as_dict() for e in find_events(mails)]
        return Result(ok=True, output=found, untrusted=True,
                      detail=f"found {len(found)} upcoming item(s) in {len(mails)} email(s)")

    def undo(self, result: Result, ctx: Context) -> None:
        """Nothing was changed, so there is nothing to undo."""
        return None


def _event_body(e: CalendarEventArgs, reminders: list[int]) -> dict:
    """Build the Google Calendar event: title, time (or all-day), pop-up reminders, a description
    saying which email it came from, and a hidden tag used to avoid duplicates.
    """
    from datetime import date, timedelta

    if e.all_day:
        d = date.fromisoformat(e.start[:10])
        start = {"date": d.isoformat()}
        end = {"date": (d + timedelta(days=1)).isoformat()}
    else:
        start = {"dateTime": e.start}
        end = {"dateTime": e.end or e.start}
    sender = e.source_from.split("<")[0].strip(' "') or e.source_from
    return {
        "summary": e.title,
        "location": e.location,
        "description": (f"Added by MAESTRO from the email \"{e.source_subject}\" "
                        f"({sender}).\nFound in: {e.evidence}\n\n"
                        "Check the original email before relying on this."),
        "start": start, "end": end,
        "reminders": {"useDefault": False, "overrides": [
            {"method": "popup", "minutes": max(0, min(m, 40320))} for m in reminders]},
        "extendedProperties": {"private": {"maestro_source": _source_key(e)}},
    }


def _source_key(e: CalendarEventArgs) -> str:
    """A stable id for an event (email id + start time) so running the request twice does not add
    it twice.
    """
    return f"{e.source}:{e.start[:16]}"


def _events(a: AddEventsArgs) -> list[CalendarEventArgs]:
    """Validate each event that came out of the emails, field by field."""
    return [CalendarEventArgs.model_validate(x) for x in a.events if isinstance(x, dict)]


class AddEventsExecutor:
    verb = "calendar.add_events"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        """Preview: list every event that would be added, so the user approves the actual items,
        not just a count.
        """
        from maestro.google.events import FoundEvent

        try:
            events = _events(AddEventsArgs.model_validate(resolve(args, ctx)))
        except Exception:
            events = None
        if events is None:
            return EffectManifest(summary="Add reminders to your Google Calendar",
                                  unknowns=["which events depends on the emails"])
        if not events:
            return EffectManifest(summary="No upcoming tests, tickets or deadlines found "
                                          "in those emails — nothing to add")
        return EffectManifest(
            summary=f"Add {len(events)} reminder(s) to your Google Calendar",
            items=[FoundEvent(**e.model_dump()).describe() for e in events],
            external=["Google Calendar (your own calendar; no guests, nobody is emailed)"],
            unknowns=["dates were read from emails: check them against the originals"])

    def execute(self, args: dict, ctx: Context) -> Result:
        """Add the events to the user's own calendar with reminders, with no guests, skipping any
        already added.
        """
        a = AddEventsArgs.model_validate(resolve(args, ctx))
        events = _events(a)
        if len(events) > MAX_EVENTS:
            return Result(ok=False, detail=f"refusing to add more than {MAX_EVENTS} events")
        created: list[dict] = []
        skipped = 0
        try:
            for e in events:
                if _google().calendar_find(_source_key(e)):
                    skipped += 1          # already added on an earlier run
                    continue
                made = _google().calendar_add(_event_body(e, a.reminders))
                made["when"] = e.start
                created.append(made)
        except GoogleNotConnected as err:
            return _not_connected(err)
        except Exception as err:
            return Result(ok=False, detail=f"calendar update failed: {err}",
                          undo_data={"created": created})
        note = f" ({skipped} already in your calendar)" if skipped else ""
        return Result(ok=True, output=created, undo_data={"created": created},
                      detail=f"added {len(created)} reminder(s) to Google Calendar{note}")

    def undo(self, result: Result, ctx: Context) -> None:
        """Delete the events this run added."""
        for e in (result.undo_data or {}).get("created", []):
            _google().calendar_delete_own(e["id"])


for _ex in (MailSearchExecutor(), MailReadExecutor(), MailDraftExecutor(),
            DriveSearchExecutor(), DriveDownloadExecutor(), DriveUploadExecutor(),
            FindEventsExecutor(), AddEventsExecutor()):
    register_executor(_ex)
