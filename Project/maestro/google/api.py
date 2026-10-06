"""Every call MAESTRO makes to Google, in one small adapter.

The executors never touch `googleapiclient` directly; they call these eight
methods. That keeps the surface auditable at a glance — there is no `send`,
no `share`, no `delete` of anything MAESTRO did not create — and lets the test
suite substitute `set_api(FakeGoogle())` so it runs with no network and no
account.

Anything returned from Gmail or Drive (subjects, bodies, file names) is written
by other people and is UNTRUSTED (docs/02 §6). Names are sanitised before they
touch the filesystem; contents are only ever displayed or summarised.
"""

from __future__ import annotations

import base64
import fnmatch
import html
import io
import mimetypes
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from maestro.google.auth import GoogleNotConnected, credentials

BODY_LIMIT = 6000          # characters of an email body we keep
GOOGLE_EXPORT = {           # Google Docs formats have no bytes; export them
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}


class GoogleAPI(Protocol):
    def search_mail(self, query: str, limit: int) -> list[dict]:
        """Return up to `limit` messages matching a Gmail search query (sender, subject, date,
        snippet).
        """

    def read_mail(self, message_id: str) -> dict:
        """Return one email's sender, subject, date, body text and attachment names."""

    def create_draft(self, to: list[str], subject: str, body: str) -> dict:
        """Save an unsent email in Gmail Drafts and return its draft id."""

    def delete_draft(self, draft_id: str) -> None:
        """Delete a draft MAESTRO created (used for undo)."""

    def search_drive(self, name: str, limit: int, kind: str | None) -> list[dict]:
        """Return Drive files matching a name and/or kind."""

    def download(self, file_id: str, dest_dir: Path) -> Path:
        """Download one Drive file into a folder and return the saved path."""

    def upload(self, path: Path, folder_id: str | None) -> dict:
        """Upload one local file to the user's Drive and return its id, name and link."""

    def trash_own_upload(self, file_id: str) -> None:
        """Move a file MAESTRO uploaded to the Drive trash (used for undo)."""

    def calendar_find(self, source_key: str) -> list[dict]:
        """Return calendar events MAESTRO already added for this source, to avoid duplicates."""

    def calendar_add(self, event: dict) -> dict:
        """Add one event to the user's primary calendar and return its id and link."""

    def calendar_delete_own(self, event_id: str) -> None:
        """Delete an event MAESTRO added (used for undo)."""


_API: GoogleAPI | None = None


def set_api(api: GoogleAPI | None) -> None:
    """Tests: substitute a fake. None restores the live client."""
    global _API
    _API = api


def api() -> GoogleAPI:
    """The Google adapter in use: the live one by default, or the fake one a test installed."""
    global _API
    if _API is None:
        _API = LiveGoogle()
    return _API


# --------------------------------------------------------------------------- #
# helpers shared with the fake
# --------------------------------------------------------------------------- #

_UNSAFE_NAMES = ["*.key", "*.pem", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", ".env",
                 "*.env", "*.kdbx", "credentials", "token.json", "client_secret*"]


def safe_filename(name: str, fallback: str = "drive-file") -> str:
    """A Drive file name is attacker-controlled: '../../.ssh/authorized_keys'
    must become a plain name inside the destination folder, never a path."""
    base = re.split(r"[/\\]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", base).strip(" .")
    if not base:
        base = fallback
    if any(fnmatch.fnmatch(base.lower(), p) for p in _UNSAFE_NAMES):
        base = f"drive_{base}.txt"   # never lands with a credential-looking name
    return base[:180]


def unique_path(target: Path) -> Path:
    """Return the path if free, otherwise 'name (1).ext', 'name (2).ext', ... so nothing is
    overwritten.
    """
    if not target.exists():
        return target
    for i in range(1, 1000):
        cand = target.with_name(f"{target.stem} ({i}){target.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(target)


def drive_quote(s: str) -> str:
    """Escape a value for Drive's query language ('name contains ...')."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def json_ld(html_text: str) -> list[dict]:
    """schema.org blocks (FlightReservation, TrainReservation...) in an email."""
    import json

    out: list[dict] = []
    for block in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                            html_text or ""):
        try:
            data = json.loads(html.unescape(block).strip())
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                out.append(item)
    return out


def _html_to_text(s: str) -> str:
    """Strip an HTML email down to plain text (drop scripts and styles, keep line breaks)."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


# --------------------------------------------------------------------------- #
# the live client
# --------------------------------------------------------------------------- #


class LiveGoogle:
    def __init__(self) -> None:
        """Start with no Google connections; each service connects on first use."""
        self._gmail = None
        self._drive = None

    def _svc(self, name: str, version: str):
        """Build a Google API client for one service using the stored sign-in."""
        try:
            from googleapiclient.discovery import build
        except ImportError as e:
            raise GoogleNotConnected(
                "Google support is not installed: pip install -e \".[google]\"") from e
        return build(name, version, credentials=credentials(), cache_discovery=False)

    @property
    def gmail(self):
        """The Gmail client, created on first use."""
        if self._gmail is None:
            self._gmail = self._svc("gmail", "v1")
        return self._gmail

    @property
    def drive(self):
        """The Drive client, created on first use."""
        if self._drive is None:
            self._drive = self._svc("drive", "v3")
        return self._drive

    # -- Gmail ---------------------------------------------------------------

    def search_mail(self, query: str, limit: int) -> list[dict]:
        """Run a Gmail search and fetch each match's sender, subject, date, snippet and unread
        flag.
        """
        res = self.gmail.users().messages().list(
            userId="me", q=query, maxResults=limit).execute()
        out = []
        for ref in res.get("messages", [])[:limit]:
            m = self.gmail.users().messages().get(
                userId="me", id=ref["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]).execute()
            h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "from": h.get("from", ""),
                        "subject": h.get("subject", "(no subject)"),
                        "date": h.get("date", ""), "snippet": html.unescape(m.get("snippet", "")),
                        "unread": "UNREAD" in m.get("labelIds", [])})
        return out

    def read_mail(self, message_id: str) -> dict:
        """Fetch one email and return its headers, plain-text body (from HTML if needed),
        attachment names and any embedded booking data.
        """
        m = self.gmail.users().messages().get(userId="me", id=message_id,
                                              format="full").execute()
        payload = m.get("payload", {})
        h = {x["name"].lower(): x["value"] for x in payload.get("headers", [])}
        plain, htmls, attachments = [], [], []

        def walk(part):
            """Visit each part of the email, collecting plain-text and HTML bodies and attachment
            names.
            """
            mime = part.get("mimeType", "")
            data = part.get("body", {}).get("data")
            if part.get("filename"):
                attachments.append(part["filename"])
            elif data and mime == "text/plain":
                plain.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
            elif data and mime == "text/html":
                htmls.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
            for p in part.get("parts", []) or []:
                walk(p)

        walk(payload)
        body = "\n".join(plain) if plain else _html_to_text("\n".join(htmls))
        return {"id": m["id"], "from": h.get("from", ""), "to": h.get("to", ""),
                "subject": h.get("subject", "(no subject)"), "date": h.get("date", ""),
                "body": body.strip()[:BODY_LIMIT], "attachments": attachments,
                "structured": json_ld("\n".join(htmls))}

    def create_draft(self, to: list[str], subject: str, body: str) -> dict:
        """Build the email and save it to Gmail Drafts (the API call is drafts.create, never send).
        """
        msg = EmailMessage()
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(bytes(msg)).decode()
        d = self.gmail.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}).execute()
        return {"draft_id": d["id"], "message_id": d.get("message", {}).get("id", "")}

    def delete_draft(self, draft_id: str) -> None:
        """Delete a draft by id."""
        self.gmail.users().drafts().delete(userId="me", id=draft_id).execute()

    # -- Drive ---------------------------------------------------------------

    def search_drive(self, name: str, limit: int, kind: str | None) -> list[dict]:
        """Search Drive by name and kind, newest first, excluding trashed files. The name is
        escaped for Drive's query language.
        """
        q = ["trashed = false"]
        if name:
            q.append(f"name contains '{drive_quote(name)}'")
        if kind == "folder":
            q.append("mimeType = 'application/vnd.google-apps.folder'")
        elif kind:
            q.append(f"(mimeType contains '{drive_quote(kind)}' or "
                     f"name contains '.{drive_quote(kind)}')")
        res = self.drive.files().list(
            q=" and ".join(q), pageSize=limit, orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,size,webViewLink)").execute()
        return [{"id": f["id"], "name": f["name"], "mime": f.get("mimeType", ""),
                 "modified": f.get("modifiedTime", ""), "size": int(f.get("size", 0) or 0),
                 "link": f.get("webViewLink", "")} for f in res.get("files", [])]

    def download(self, file_id: str, dest_dir: Path) -> Path:
        """Download one file (exporting Google Docs/Sheets/Slides as PDF) under a safe, unique file
        name.
        """
        from googleapiclient.http import MediaIoBaseDownload

        meta = self.drive.files().get(fileId=file_id, fields="name,mimeType").execute()
        name = safe_filename(meta["name"])
        export = GOOGLE_EXPORT.get(meta.get("mimeType", ""))
        if export:
            request = self.drive.files().export_media(fileId=file_id, mimeType=export[0])
            if not name.lower().endswith(export[1]):
                name += export[1]
        elif meta.get("mimeType", "").startswith("application/vnd.google-apps"):
            raise ValueError(f"{meta['name']!r} is a Google {meta['mimeType']} and "
                             "cannot be downloaded as a file")
        else:
            request = self.drive.files().get_media(fileId=file_id)
        target = unique_path(dest_dir / name)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        target.write_bytes(buf.getvalue())
        return target

    def upload(self, path: Path, folder_id: str | None) -> dict:
        """Upload one file to the user's Drive, optionally into a folder."""
        from googleapiclient.http import MediaFileUpload

        body = {"name": path.name}
        if folder_id:
            body["parents"] = [folder_id]
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        f = self.drive.files().create(
            body=body, media_body=MediaFileUpload(str(path), mimetype=mime, resumable=True),
            fields="id,name,webViewLink").execute()
        return {"id": f["id"], "name": f["name"], "link": f.get("webViewLink", "")}

    def trash_own_upload(self, file_id: str) -> None:
        """Move a file MAESTRO uploaded to the Drive trash. The drive.file permission only allows
        this for MAESTRO's own uploads.
        """
        # drive.file scope: this only works on files MAESTRO itself uploaded.
        self.drive.files().update(fileId=file_id, body={"trashed": True}).execute()

    # -- Calendar ------------------------------------------------------------

    @property
    def calendar(self):
        """The Calendar client, created on first use."""
        if getattr(self, "_calendar", None) is None:
            self._calendar = self._svc("calendar", "v3")
        return self._calendar

    def calendar_find(self, source_key: str) -> list[dict]:
        """Events MAESTRO already added for this email, so a re-run adds none."""
        res = self.calendar.events().list(
            calendarId="primary", privateExtendedProperty=f"maestro_source={source_key}",
            maxResults=10, singleEvents=True).execute()
        return res.get("items", [])

    def calendar_add(self, event: dict) -> dict:
        """Insert an event into the primary calendar with no attendees and sendUpdates='none', so
        nobody is emailed.
        """
        # sendUpdates="none" and no attendees: nobody is ever emailed.
        body = {k: v for k, v in event.items() if k != "attendees"}
        e = self.calendar.events().insert(calendarId="primary", body=body,
                                          sendUpdates="none").execute()
        return {"id": e["id"], "link": e.get("htmlLink", ""), "summary": e.get("summary", "")}

    def calendar_delete_own(self, event_id: str) -> None:
        """Delete an event by id without notifying anyone."""
        self.calendar.events().delete(calendarId="primary", eventId=event_id,
                                      sendUpdates="none").execute()
