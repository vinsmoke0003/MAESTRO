"""Deterministic routing for Google Gmail / Drive requests.

The trained intent classifier keeps its 16 classes, so DeskPlan and every
reported number stay exactly as they were. Requests that name a Google service
are recognised here instead, by rules that can be read and tested line by line:

    "any unread emails from Priya?"             -> GMAIL_SEARCH
    "read my latest email from the bank"        -> GMAIL_READ
    "draft a gmail to a@b.com about the report" -> GMAIL_DRAFT
    "check my last 15 emails for tests or tickets
     and add reminders to my calendar"          -> GMAIL_TO_CALENDAR
    "find my resume in google drive"            -> DRIVE_SEARCH
    "download the DSA notes from my drive"      -> DRIVE_DOWNLOAD
    "upload report.pdf from downloads to drive" -> DRIVE_UPLOAD
    "share my drive folder with x@y.com"        -> DRIVE_SHARE  (then BLOCKED)
    "delete old files from google drive"        -> DRIVE_DELETE (then BLOCKED)

The router runs AFTER the safety prefilter: a confident refusal ("send an
email to x@y.com", "email my ssh key to ...") is never re-routed. It is also
conservative about the word "drive" — a hard drive, USB drive or "C drive" is
not Google Drive — and about "email": a plain "draft an email to Sam" still
writes a local draft, exactly as before; Gmail is used when you say Gmail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from maestro.nlp.intents import (
    DRIVE_DELETE,
    DRIVE_DOWNLOAD,
    DRIVE_SEARCH,
    DRIVE_SHARE,
    DRIVE_UPLOAD,
    GMAIL_DRAFT,
    GMAIL_READ,
    GMAIL_SEARCH,
    GMAIL_TO_CALENDAR,
)

_I = re.I

NOT_GOOGLE_DRIVE = re.compile(
    r"\b(hard|usb|external|flash|pen|thumb|disk|disc|network|shared|[a-z])\s+drive\b"
    r"|\bdrive\s+(space|letter)\b", _I)
DRIVE = re.compile(r"\bgoogle\s+drive\b|\bg-?drive\b|\bmy\s+drive\b"
                   r"|\b(?:to|from|in|on|into|onto)\s+(?:the\s+)?drive\b", _I)
GMAIL_EXPLICIT = re.compile(r"\bg-?mail\b", _I)
MAIL = re.compile(
    r"\bg-?mail\b|\be-?mails\b|\bmails\b|\bmy\s+e-?mail\b"
    r"|\b(?:check|open|read|show)\s+(?:my\s+|the\s+)?(?:e-?mail\s+)?inbox\b"
    r"|\b(?:my|gmail|e-?mail|mail)\s+inbox\b"
    r"|\be-?mails?\s+from\b|\b(?:new|latest|last|recent|unread)\s+e-?mails?\b", _I)
# "inbox" is also a local folder alias (~/maestro_workspace/inbox). A request
# about files in it is a file task, not Gmail, unless Gmail is named outright.
FILE_TASK = re.compile(r"\b(files?|folders?|pdfs?|docx?|zips?|images?|screenshots?|move|copy"
                       r"|rename|organi[sz]e|archive|sort|trash)\b", _I)

SHARE = re.compile(r"\bshare\b|\bmake\b[^.?!]{0,30}\bpublic\b|\bgive\b[^.?!]{0,20}\baccess\b"
                   r"|\bsend\b[^.?!]{0,20}\blink\b", _I)
DELETE = re.compile(r"\b(delete|remove|trash|erase|wipe|get\s+rid\s+of|clear\s+out)\b", _I)
UPLOAD = re.compile(r"\b(upload|back\s*up|put|save|copy|move|send)\b[^.?!]*"
                    r"\b(to|into|onto|on)\s+(?:the\s+|my\s+)?(?:google\s+)?drive\b"
                    r"|\bupload\b", _I)
DOWNLOAD = re.compile(r"\b(download|fetch|pull|grab|get|bring|save|copy)\b[^.?!]*"
                      r"\bfrom\s+(?:the\s+|my\s+)?(?:google\s+)?drive\b|\bdownload\b", _I)
DRAFT = re.compile(r"\b(draft|compose|write|prepare)\b", _I)
READ = re.compile(r"\b(read|open|what\s+(?:does|did|do)|summari[sz]e|tell\s+me\s+what)\b"
                  r"|\bshow\s+me\s+(?:the\s+)?(?:latest|last|newest)\b", _I)
CALENDAR = re.compile(r"\bcalend[ae]rs?\b|\bremind(?:er|ers)?\b|\bschedule\b|\bagenda\b"
                      r"|\bdiary\b", _I)
CALENDAR_LIMIT = 15      # "only from the last 15 emails"
EMAIL_ADDR = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

_STOP = {
    "a", "an", "the", "my", "me", "i", "to", "from", "in", "on", "into", "onto", "of",
    "for", "with", "and", "or", "please", "can", "could", "you", "would", "will", "find",
    "search", "look", "show", "list", "get", "fetch", "download", "upload", "pull", "grab",
    "give", "bring", "save", "copy", "put", "back", "up", "google", "drive", "gdrive",
    "file", "files", "document", "documents", "doc", "docs", "folder", "folders", "called",
    "named", "that", "is", "are", "there", "any", "all", "some", "what", "which", "where",
    "do", "does", "have", "has", "it", "them", "this", "these", "those", "maestro", "hey",
    "ok", "okay", "tell", "about",
}


@dataclass
class Route:
    intent: str
    slots: dict = field(default_factory=dict)

    def apply(self, slots) -> None:
        for k, v in self.slots.items():
            if v is None or v == [] or v == "":
                continue
            setattr(slots, k, v)
            if k in getattr(slots, "unresolved", []):
                slots.unresolved.remove(k)


def mentions_drive(text: str) -> bool:
    if not DRIVE.search(text):
        return False
    # "copy it to the C drive" / "my hard drive" are local disks.
    stripped = NOT_GOOGLE_DRIVE.sub(" ", text)
    return bool(DRIVE.search(stripped)) or bool(re.search(r"\bgoogle\s+drive\b", text, _I))


def route(text: str) -> Route | None:
    """Return a Route for a Gmail / Drive request, or None to leave it alone."""
    if mentions_drive(text):
        return _drive(text)
    if MAIL.search(text) and (GMAIL_EXPLICIT.search(text) or not FILE_TASK.search(text)):
        return _gmail(text)
    return None


# --------------------------------------------------------------------------- #
# Drive
# --------------------------------------------------------------------------- #


def _drive(text: str) -> Route:
    kind = _kind(text)
    name = _name_query(text)
    if SHARE.search(text):
        return Route(DRIVE_SHARE, {"query": name, "recipients": EMAIL_ADDR.findall(text)})
    if DELETE.search(text):
        return Route(DRIVE_DELETE, {"query": name})
    to_drive = re.search(r"\b(to|into|onto)\s+(?:the\s+|my\s+)?(?:google\s+)?drive\b", text, _I)
    from_drive = re.search(r"\bfrom\s+(?:the\s+|my\s+)?(?:google\s+)?drive\b", text, _I)
    if to_drive and UPLOAD.search(text):
        return Route(DRIVE_UPLOAD, {"file_type": _ext(kind)})
    if (from_drive and DOWNLOAD.search(text)) or re.search(r"\bdownload\b", text, _I):
        return Route(DRIVE_DOWNLOAD, {"query": name or kind, "file_type": _ext(kind)})
    return Route(DRIVE_SEARCH, {"query": name, "file_type": _ext(kind)})


def _kind(text: str) -> str | None:
    m = re.search(r"\b(pdfs?|folders?|docx?|spreadsheets?|sheets?|slides?|presentations?"
                  r"|ppts?|pptx|images?|photos?|pictures?|zip)\b", text, _I)
    if not m:
        return None
    w = m.group(1).lower().rstrip("s")
    return {"doc": "document", "docx": "document", "sheet": "spreadsheet",
            "slide": "presentation", "ppt": "presentation", "pptx": "presentation",
            "photo": "image", "picture": "image"}.get(w, w)


def _ext(kind: str | None) -> str | None:
    return {"pdf": "pdf", "document": "docx", "spreadsheet": "xlsx",
            "presentation": "pptx", "zip": "zip"}.get(kind or "")


def _name_query(text: str) -> str | None:
    quoted = re.search(r"[\"“']([^\"”']{2,60})[\"”']", text)
    if quoted:
        return quoted.group(1).strip()
    called = re.search(r"\b(?:called|named|titled)\s+([\w .-]{2,60}?)(?:\s+(?:from|in|on|to)\b|$)",
                       text, _I)
    if called:
        return called.group(1).strip(" .")
    text = EMAIL_ADDR.sub(" ", text)
    words = [w for w in re.findall(r"[A-Za-z0-9_.-]+", text)
             if w.lower() not in _STOP and _kind(w) is None and not EMAIL_ADDR.match(w)
             and not re.fullmatch(r"(share|delete|remove|trash|erase|wipe|public|access|link"
                                  r"|latest|recent|new|old|last|my|with)", w, _I)]
    q = " ".join(words[:4]).strip(" .")
    return q or None


# --------------------------------------------------------------------------- #
# Gmail
# --------------------------------------------------------------------------- #


def _gmail(text: str) -> Route | None:
    if CALENDAR.search(text):
        return Route(GMAIL_TO_CALENDAR,
                     {"query": "in:inbox", "quantity": min(_count(text) or CALENDAR_LIMIT,
                                                           CALENDAR_LIMIT)})
    if DRAFT.search(text):
        if not GMAIL_EXPLICIT.search(text):
            return None               # "draft an email" stays a local .eml draft
        subject = _about(text)
        return Route(GMAIL_DRAFT, {"recipients": EMAIL_ADDR.findall(text),
                                   "subject": subject or "Draft from MAESTRO"})
    query = gmail_query(text)
    n = _count(text)
    if READ.search(text):
        return Route(GMAIL_READ, {"query": query, "quantity": min(n or 1, 5)})
    return Route(GMAIL_SEARCH, {"query": query, "quantity": min(n or 10, 50)})


def gmail_query(text: str) -> str:
    """Build Gmail search syntax from plain English."""
    parts: list[str] = []
    sender = re.search(r"\bfrom\s+(?:the\s+|my\s+|our\s+)?([\w.+@-]+(?:\s+(?!about|with|today|this|in|on|last|that"
                       r"|regarding|since|yesterday)[A-Z][\w-]*)?)", text)
    if sender and sender.group(1).lower() not in ("my", "the", "google", "gmail"):
        who = sender.group(1)
        parts.append(f'from:"{who}"' if " " in who else f"from:{who}")
    if re.search(r"\bunread\b|\bnew\s+e-?mails?\b|\bhaven'?t\s+read\b", text, _I):
        parts.append("is:unread")
    if re.search(r"\battach(ment|ed)s?\b", text, _I):
        parts.append("has:attachment")
    if re.search(r"\btoday\b", text, _I):
        parts.append("newer_than:1d")
    elif re.search(r"\byesterday\b", text, _I):
        parts.append("newer_than:2d")
    elif re.search(r"\b(this|last|past)\s+week\b", text, _I):
        parts.append("newer_than:7d")
    elif re.search(r"\b(this|last|past)\s+month\b", text, _I):
        parts.append("newer_than:30d")
    topic = _about(text)
    if topic:
        parts.append(f'subject:"{topic}"' if " " in topic else f"subject:{topic}")
    if re.search(r"\bstarred\b", text, _I):
        parts.append("is:starred")
    return " ".join(parts) or "in:inbox"


def _about(text: str) -> str | None:
    m = re.search(r"\b(?:about|regarding|re:|on the topic of|subject)\s+(?:the\s+|my\s+|our\s+)?"
                  r"([\w .'-]{2,60}?)(?:\s+(?:from|to|today|yesterday|this|last)\b|[?.!]|$)",
                  text, _I)
    return m.group(1).strip(" .'") if m else None


def _count(text: str) -> int | None:
    m = re.search(r"\b(?:last|latest|recent|top|first)\s+(\d{1,2})\b|\b(\d{1,2})\s+"
                  r"(?:latest|recent|new|unread)?\s*e-?mails?\b", text, _I)
    if m:
        return int(m.group(1) or m.group(2))
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}
    m = re.search(r"\b(?:last|latest)\s+(one|two|three|four|five|ten)\b", text, _I)
    return words[m.group(1).lower()] if m else None
