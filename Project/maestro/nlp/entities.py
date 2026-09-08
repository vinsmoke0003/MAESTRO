"""Stage 2 — entity / slot extraction (docs/05-NLP-AND-TRAINING.md §1).

Regex + gazetteer, with a documented resolution order per slot:

    explicit in the instruction -> memory/preferences -> OS defaults -> ASK

The last step is the important one. There is no "reasonable default" branch for
a destination path: an unresolved `destination` becomes a clarifying question,
never a guess. Guessing here would relocate the project's headline failure mode
into its own NLP layer.

spaCy is supported but optional (`use_spacy=True`): the rule extractor is the
baseline row in the docs/05 §4 comparison table, and it is what CI runs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------- #
# gazetteers
# --------------------------------------------------------------------------- #

FOLDER_ALIASES: dict[str, str] = {
    "downloads": "~/Downloads",
    "download folder": "~/Downloads",
    "desktop": "~/Desktop",
    "documents": "~/Documents",
    "docs folder": "~/Documents",
    "pictures": "~/Pictures",
    "photos": "~/Pictures",
    "music": "~/Music",
    "videos": "~/Videos",
    "movies": "~/Videos",
    "workspace": "~/maestro_workspace",
    "maestro workspace": "~/maestro_workspace",
    "inbox": "~/maestro_workspace/inbox",
    "archive": "~/maestro_workspace/archive",
    "invoices": "~/Documents/Invoices",
    "finance": "~/Documents/Finance",
    "finance folder": "~/Documents/Finance",
    "receipts": "~/Documents/Receipts",
    "reports": "~/Documents/Reports",
    "screenshots": "~/Pictures/Screenshots",
    "semester": "~/Documents/Semester",
    "assignments": "~/Documents/Assignments",
    "notes": "~/Documents/Notes",
    "projects": "~/Documents/Projects",
    "home": "~",
    "home folder": "~",
}

FILE_TYPES: dict[str, str] = {
    "pdf": "pdf", "pdfs": "pdf",
    "doc": "docx", "docs": "docx", "word": "docx", "docx": "docx",
    "xls": "xlsx", "excel": "xlsx", "spreadsheet": "xlsx", "spreadsheets": "xlsx",
    "xlsx": "xlsx", "csv": "csv", "csvs": "csv",
    "ppt": "pptx", "pptx": "pptx", "presentation": "pptx", "presentations": "pptx",
    "slides": "pptx", "deck": "pptx",
    "png": "png", "pngs": "png", "jpg": "jpg", "jpeg": "jpg", "jpgs": "jpg",
    "image": "png", "images": "png", "picture": "png", "pictures": "png",
    "photo": "jpg", "photos": "jpg", "screenshot": "png", "screenshots": "png",
    "txt": "txt", "text file": "txt", "text files": "txt",
    "md": "md", "markdown": "md",
    "zip": "zip", "zips": "zip", "archive file": "zip",
    "mp3": "mp3", "mp4": "mp4", "video": "mp4", "videos": "mp4",
    "json": "json", "log": "log", "logs": "log", "py": "py", "python file": "py",
    "invoice": "pdf", "invoices": "pdf", "receipt": "pdf", "receipts": "pdf",
    "report": "pdf", "reports": "pdf", "resume": "pdf", "cv": "pdf",
}

APP_NAMES: tuple[str, ...] = (
    "chrome", "google chrome", "firefox", "edge", "microsoft edge", "safari",
    "vs code", "vscode", "visual studio code", "terminal", "iterm", "notepad",
    "calculator", "spotify", "finder", "explorer", "file explorer", "notes",
    "word", "excel", "powerpoint", "preview", "textedit", "mail",
)

METRICS: dict[str, str] = {
    "disk": "disk", "disk space": "disk", "storage": "disk", "free space": "disk",
    "space left": "disk", "hard drive": "disk",
    "memory": "memory", "ram": "memory",
    "battery": "battery", "charge": "battery",
    "cpu": "cpu", "processor": "cpu",
    "os": "os", "operating system": "os", "version": "os",
    "time": "time", "date": "time", "clock": "time",
    "volume": "volume", "network": "network", "internet": "network",
}

SETTING_KEYS: dict[str, str] = {
    "volume": "volume", "sound": "volume", "audio": "volume",
}

# Words that make an instruction destructive; used by the rule intent baseline
# and by the dataset generator's safety labelling.
DESTRUCTIVE_WORDS = ("delete", "remove", "erase", "wipe", "trash", "clear out",
                     "get rid of", "purge", "shred", "destroy")

ENTITY_TYPES = (
    "PATH", "FILE_TYPE", "FILE_NAME", "APP_NAME", "URL", "DATETIME", "DURATION",
    "QUANTITY", "PERSON", "EMAIL", "SETTING_KEY", "SETTING_VALUE", "WORKSPACE_REF",
)

# --------------------------------------------------------------------------- #
# patterns
# --------------------------------------------------------------------------- #

URL_RE = re.compile(r"\bhttps?://[^\s'\"<>]+", re.I)
BARE_DOMAIN_RE = re.compile(
    r"\b((?:[a-z0-9-]+\.)+(?:com|org|net|edu|in|io|dev|ac\.in|co\.in|gov))(/[^\s'\"]*)?\b", re.I
)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
EXPLICIT_PATH_RE = re.compile(
    r"(?:^|[\s'\"(])((?:~|\$HOME|/|[A-Za-z]:[\\/])[^\s'\"()<>,;]*)"
)
# "Documents/Invoices", "Downloads\\2026" — a known folder name followed by
# sub-folders. Without this the gazetteer would match only "Documents" and
# silently drop the part of the destination the user actually cares about.
RELATIVE_PATH_RE = re.compile(
    r"\b([A-Za-z][\w ]{1,20})[\\/]([\w][\w \-./\\]{0,60})", re.I
)
FILENAME_RE = re.compile(r"\b([\w][\w \-()]{0,60}\.[A-Za-z0-9]{1,6})\b")
GROUP_BY_TYPE_RE = re.compile(
    r"\bby\s+(?:file\s+)?(?:type|types|extension|extensions|format|kind)\b"
    r"|\binto\s+(?:sub)?folders?\s+by\b"
    r"|\bgroup(?:ed)?\s+by\s+type\b"
    r"|\bsort(?:ed)?\s+by\s+(?:file\s+)?type\b",
    re.I,
)
QUANTITY_RE = re.compile(r"\b(\d+)\s*(files?|items?|photos?|documents?|pdfs?|copies)\b", re.I)
# No trailing \b: "%" is not a word character, so `%\b` can never match at the
# end of a string — "set the volume to 30%" silently produced no SETTING_VALUE
# and the whole instruction turned into a clarifying question.
PERCENT_RE = re.compile(r"\b(\d{1,3})\s*(?:%|\bpercent\b)", re.I)
DURATION_RE = re.compile(
    r"\b(?:last|past|previous|next)\s+(\d+)?\s*(day|days|week|weeks|month|months|"
    r"year|years|hour|hours)\b", re.I
)
DATE_WORDS = {
    "today": 0, "yesterday": -1, "tomorrow": 1,
}
MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")


@dataclass(frozen=True)
class Entity:
    type: str
    value: str
    span: tuple[int, int]
    raw: str = ""
    source: str = "rule"  # rule | gazetteer | memory | default

    def as_dict(self) -> dict:
        return {"type": self.type, "value": self.value, "span": list(self.span),
                "raw": self.raw or self.value, "source": self.source}


@dataclass
class Slots:
    """The resolved slot view the planner actually consumes."""

    source: str | None = None
    destination: str | None = None
    file_type: str | None = None
    file_name: str | None = None
    app: str | None = None
    url: str | None = None
    metric: str | None = None
    setting_key: str | None = None
    setting_value: str | None = None
    subject: str | None = None
    recipients: list[str] = field(default_factory=list)
    quantity: int | None = None
    since: str | None = None  # ISO date, resolved from a DATETIME/DURATION entity
    days: int | None = None
    recursive: bool = False
    group_by: str | None = None  # "type" -> organise into per-extension subfolders
    entities: list[Entity] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "entities"}
        d["entities"] = [e.as_dict() for e in self.entities]
        return d


class EntityExtractor:
    """Rule + gazetteer extractor. `known_paths` comes from learned preferences."""

    def __init__(self, known_paths: dict[str, str] | None = None,
                 today: date | None = None, use_spacy: bool = False):
        self.known_paths = {k.lower(): v for k, v in (known_paths or {}).items()}
        self.today = today or date.today()
        self._nlp = _load_spacy() if use_spacy else None

    # -- entity level ------------------------------------------------------

    def extract(self, text: str) -> list[Entity]:
        ents: list[Entity] = []
        low = text.lower()

        for m in URL_RE.finditer(text):
            ents.append(Entity("URL", m.group(0).rstrip(".,);"), m.span(), m.group(0)))
        if not ents:
            for m in BARE_DOMAIN_RE.finditer(text):
                if "@" in text[max(0, m.start() - 1): m.start() + 1]:
                    continue
                ents.append(Entity("URL", "https://" + m.group(0), m.span(), m.group(0)))

        for m in EMAIL_RE.finditer(text):
            ents.append(Entity("EMAIL", m.group(0), m.span(), m.group(0)))

        for m in EXPLICIT_PATH_RE.finditer(text):
            raw = m.group(1)
            if URL_RE.match(raw) or "@" in raw:
                continue
            ents.append(Entity("PATH", _norm_path(raw), m.span(1), raw))

        # Alias-rooted relative paths, before the bare-alias gazetteer below.
        for m in RELATIVE_PATH_RE.finditer(text):
            head_words = m.group(1).strip().lower().split()
            root = alias = None
            # Try the longest trailing phrase first: in "... to Documents/Invoices"
            # the head captured is "to documents", and "documents" is the alias.
            for k in (3, 2, 1):
                if len(head_words) < k:
                    continue
                cand = " ".join(head_words[-k:])
                root = FOLDER_ALIASES.get(cand) or self.known_paths.get(cand)
                if root:
                    alias = cand
                    break
            if root is None or alias is None:
                continue
            start = m.start(1) + len(m.group(1)) - len(alias)
            span = (start, m.end(2))
            if _overlaps(span, ents):
                continue
            tail = _norm_path(m.group(2)).strip("/")
            ents.append(Entity("PATH", f"{root}/{tail}", span, text[start:m.end(2)],
                               "gazetteer"))

        # Folder aliases — longest first so "download folder" beats "downloads".
        for alias in sorted(FOLDER_ALIASES, key=len, reverse=True):
            for m in re.finditer(rf"\b{re.escape(alias)}\b", low):
                if _overlaps(m.span(), ents):
                    continue
                ents.append(Entity("PATH", FOLDER_ALIASES[alias], m.span(),
                                   text[m.start():m.end()], "gazetteer"))

        for alias, target in self.known_paths.items():
            for m in re.finditer(rf"\b{re.escape(alias)}\b", low):
                if _overlaps(m.span(), ents):
                    continue
                ents.append(Entity("PATH", target, m.span(), text[m.start():m.end()],
                                   "memory"))

        for m in FILENAME_RE.finditer(text):
            if _overlaps(m.span(1), ents):
                continue
            ents.append(Entity("FILE_NAME", m.group(1), m.span(1), m.group(1)))

        for word in sorted(FILE_TYPES, key=len, reverse=True):
            for m in re.finditer(rf"\b{re.escape(word)}\b", low):
                if _overlaps(m.span(), ents, only={"FILE_TYPE", "PATH"}):
                    continue
                ents.append(Entity("FILE_TYPE", FILE_TYPES[word], m.span(),
                                   text[m.start():m.end()], "gazetteer"))
                break

        for app in sorted(APP_NAMES, key=len, reverse=True):
            m = re.search(rf"\b{re.escape(app)}\b", low)
            if m and not _overlaps(m.span(), ents, only={"APP_NAME"}):
                ents.append(Entity("APP_NAME", app, m.span(), text[m.start():m.end()],
                                   "gazetteer"))
                break

        for phrase in sorted(METRICS, key=len, reverse=True):
            m = re.search(rf"\b{re.escape(phrase)}\b", low)
            if m:
                ents.append(Entity("SETTING_KEY" if phrase in SETTING_KEYS else "METRIC",
                                   METRICS[phrase], m.span(), text[m.start():m.end()],
                                   "gazetteer"))
                break

        for m in QUANTITY_RE.finditer(text):
            ents.append(Entity("QUANTITY", m.group(1), m.span(), m.group(0)))
        for m in PERCENT_RE.finditer(text):
            ents.append(Entity("SETTING_VALUE", m.group(1), m.span(), m.group(0)))

        ents.extend(self._temporal(text))

        if self._nlp is not None:
            ents.extend(self._spacy_people(text, ents))

        return sorted(ents, key=lambda e: e.span[0])

    def _temporal(self, text: str) -> list[Entity]:
        out: list[Entity] = []
        low = text.lower()

        for m in DURATION_RE.finditer(text):
            n = int(m.group(1) or 1)
            unit = m.group(2).rstrip("s")
            days = {"day": 1, "week": 7, "month": 30, "year": 365, "hour": 1}[unit]
            total = max(1, n * days)
            out.append(Entity("DURATION", str(total), m.span(), m.group(0)))

        for word, delta in DATE_WORDS.items():
            m = re.search(rf"\b{word}\b", low)
            if m:
                out.append(Entity("DATETIME", (self.today + timedelta(days=delta)).isoformat(),
                                  m.span(), m.group(0)))

        m = re.search(r"\blast month\b", low)
        if m:
            first = self.today.replace(day=1)
            prev_end = first - timedelta(days=1)
            out.append(Entity("DATETIME", prev_end.strftime("%Y-%m"), m.span(), m.group(0)))
        m = re.search(r"\bthis month\b", low)
        if m:
            out.append(Entity("DATETIME", self.today.strftime("%Y-%m"), m.span(), m.group(0)))

        for name in MONTHS:
            m = re.search(rf"\b{name}\b", low)
            if m:
                month = MONTHS.index(name) + 1
                year = self.today.year if month <= self.today.month else self.today.year - 1
                out.append(Entity("DATETIME", f"{year}-{month:02d}", m.span(), m.group(0)))
                break

        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
        if m:
            out.append(Entity("DATETIME", m.group(0), m.span(), m.group(0)))
        return out

    def _spacy_people(self, text: str, existing: list[Entity]) -> list[Entity]:
        doc = self._nlp(text)  # type: ignore[misc]
        out = []
        for ent in doc.ents:
            if ent.label_ == "PERSON" and not _overlaps((ent.start_char, ent.end_char),
                                                        existing):
                out.append(Entity("PERSON", ent.text, (ent.start_char, ent.end_char),
                                  ent.text, "spacy"))
        return out

    # -- slot level --------------------------------------------------------

    def slots(self, text: str, intent: str | None = None) -> Slots:
        ents = self.extract(text)
        s = Slots(entities=ents)
        low = text.lower()

        paths = [e for e in ents if e.type == "PATH"]
        s.source, s.destination = _assign_directions(text, paths)

        ft = next((e for e in ents if e.type == "FILE_TYPE"), None)
        s.file_type = ft.value if ft else None
        fn = next((e for e in ents if e.type == "FILE_NAME"), None)
        s.file_name = fn.value if fn else None
        app = next((e for e in ents if e.type == "APP_NAME"), None)
        s.app = app.value if app else None
        url = next((e for e in ents if e.type == "URL"), None)
        s.url = url.value if url else None
        metric = next((e for e in ents if e.type == "METRIC"), None)
        s.metric = metric.value if metric else None
        sk = next((e for e in ents if e.type == "SETTING_KEY"), None)
        s.setting_key = sk.value if sk else None
        sv = next((e for e in ents if e.type == "SETTING_VALUE"), None)
        s.setting_value = sv.value if sv else None
        s.recipients = [e.value for e in ents if e.type == "EMAIL"]
        q = next((e for e in ents if e.type == "QUANTITY"), None)
        s.quantity = int(q.value) if q else None
        dur = next((e for e in ents if e.type == "DURATION"), None)
        s.days = int(dur.value) if dur else None
        dt = next((e for e in ents if e.type == "DATETIME"), None)
        s.since = dt.value if dt else None
        s.recursive = bool(re.search(r"\b(recursive|recursively|and subfolders|"
                                     r"including subfolders|all subfolders)\b", low))
        s.group_by = "type" if GROUP_BY_TYPE_RE.search(text) else None
        s.subject = _subject(text)

        if intent:
            from maestro.nlp.intents import REQUIRED_SLOTS

            for slot in REQUIRED_SLOTS.get(intent, ()):
                if not getattr(s, slot, None):
                    s.unresolved.append(slot)
        return s


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _load_spacy():  # pragma: no cover - optional dependency
    try:
        import spacy

        return spacy.load("en_core_web_sm")
    except Exception:
        return None


def _overlaps(span: tuple[int, int], ents: Iterable[Entity],
              only: set[str] | None = None) -> bool:
    a, b = span
    for e in ents:
        if only and e.type not in only:
            continue
        c, d = e.span
        if a < d and c < b:
            return True
    return False


def _norm_path(raw: str) -> str:
    t = raw.strip().strip("'\"").replace("\\", "/")
    t = t.replace("$HOME", "~")
    if len(t) > 1 and t.endswith("/"):
        t = t[:-1]
    return t


SOURCE_MARKERS = (" from ", " in ", " inside ", " within ", " under ", " out of ")
DEST_MARKERS = (" to ", " into ", " onto ", " under the ", " in the ")


def _assign_directions(text: str, paths: list[Entity]) -> tuple[str | None, str | None]:
    """Decide which path is the source and which is the destination.

    Uses the preposition immediately preceding each path. Two paths with no
    directional marker resolve as (first, second) — the reading a human gives
    "move Downloads Documents" — and a single path is a source. Neither branch
    ever *invents* the missing one; an unfilled destination stays unfilled and
    becomes a clarifying question.
    """
    if not paths:
        return None, None
    low = text.lower()
    src = dst = None
    for e in paths:
        prefix = low[max(0, e.span[0] - 14): e.span[0]]
        if any(prefix.endswith(m.strip() + " ") or m in prefix for m in (" to ", " into ",
                                                                        " onto ")):
            dst = dst or e.value
        elif any(m in prefix for m in SOURCE_MARKERS):
            src = src or e.value
    if src is None and dst is None:
        src = paths[0].value
        if len(paths) > 1:
            dst = paths[1].value
    elif src is None and len(paths) > 1:
        src = next((e.value for e in paths if e.value != dst), None)
    elif dst is None and len(paths) > 1:
        dst = next((e.value for e in paths if e.value != src), None)
    return src, dst


def _subject(text: str) -> str | None:
    m = re.search(r"\b(?:about|regarding|re:|summari[sz]ing|titled)\s+(.{3,80})", text, re.I)
    if m:
        return m.group(1).strip(" .'\"")
    m = re.search(r"\bdraft (?:an? )?(?:email|note|message)\s+(?:to\s+[\w.@+-]+\s+)?"
                  r"(?:about\s+)?(.{3,80})", text, re.I)
    if m:
        return m.group(1).strip(" .'\"")
    return None


def resolve_path(value: str | None, home: Path | None = None) -> str | None:
    """`~/Downloads` -> an absolute path string for the current user.

    `~/maestro_workspace` is special-cased onto the configured workspace, so
    MAESTRO_WORKSPACE relocates the agent's own root without the gazetteer, the
    dataset's gold plans, or the eval fixtures having to know about it. Gold
    plans stay written in the portable `~/...` form; only resolution is local.
    """
    if not value:
        return None
    from maestro.config import settings

    home = home or Path.home()
    v = value.replace("\\", "/")
    ws = str(settings().workspace).replace("\\", "/")
    if v == "~/maestro_workspace":
        return ws
    if v.startswith("~/maestro_workspace/"):
        return f"{ws}/{v[len('~/maestro_workspace/'):]}"
    if v == "~":
        return str(home)
    if v.startswith("~/"):
        return str(home / v[2:])
    return v


def utcstamp() -> str:
    return datetime.now().isoformat(timespec="seconds")
