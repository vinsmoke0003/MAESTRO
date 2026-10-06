"""Find the dated things that matter in emails: tests, flights, trains, deadlines.

Deterministic — no model reads the mail here. Two sources, in order of trust:

1. **Structured booking data.** Airlines, IRCTC, bus and event sites embed
   schema.org `FlightReservation` / `TrainReservation` / `EventReservation`
   JSON-LD in their confirmation emails (it is what makes Gmail show a
   boarding-pass card). When present, times come from there exactly.
2. **Text rules.** A date (and time, if any) within reach of a keyword that
   says what it is — "exam", "test", "viva", "flight", "PNR", "train",
   "interview", "deadline", "submit by"... — becomes a candidate.

Everything returned is UNTRUSTED (it was written by whoever sent the mail):
it is shown in the preview and needs your approval before anything touches
your calendar. A spoofed "exam at 3 AM" costs you one glance at the preview,
not a wrong calendar.

Dates are read the Indian way when ambiguous: 03/10/2026 is 3 October.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

MAX_PER_MAIL = 3
MAX_TOTAL = 12
LOOKAROUND = 220          # chars around a date searched for a keyword

CATEGORIES: list[tuple[str, str]] = [
    ("Flight", r"\bflights?\b|\bboarding\b|\bairlines?\b|\bairport\b|\bindigo\b|"
               r"\bair\s+india\b|\bvistara\b|\bspicejet\b|\bakasa\b|\bterminal\s+\d\b"),
    ("Train", r"\btrains?\b|\birctc\b|\bcoach\b|\bberth\b|\brailways?\b|\bvande\s+bharat\b|"
              r"\brajdhani\b|\bshatabdi\b|\bplatform\s+\d"),
    ("Bus", r"\bbus\b|\bredbus\b"),
    ("Exam", r"\bexam(?:ination)?s?\b|\btests?\b|\bquiz\b|\bviva\b|\bmid[- ]?terms?\b|"
             r"\bend[- ]?terms?\b|\bassessment\b|\bpracticals?\b|\bhall\s+ticket\b|"
             r"\badmit\s+card\b"),
    ("Interview", r"\binterview\b|\bassessment\s+centre\b"),
    ("Deadline", r"\bdeadline\b|\bdue\s+(?:date|on|by)\b|\blast\s+date\b|\bsubmit\w*\s+by\b|"
                 r"\bsubmission\b"),
    ("Appointment", r"\bappointment\b|\bconsultation\b|\bdoctor\b|\bclinic\b"),
    ("Event", r"\bbooking\s+confirmed\b|\bticket\b|\bconcert\b|\bshow\s+time\b|"
              r"\bbookmyshow\b|\bwebinar\b|\bmeeting\b|\bseminar\b|\bworkshop\b"),
]
_CAT = [(name, re.compile(p, re.I)) for name, p in CATEGORIES]
PNR = re.compile(r"\bPNR[:\s#]*([A-Z0-9]{5,10})\b", re.I)

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
DATE_PATTERNS = [
    ("dmy_name", re.compile(rf"\b{_DAY}[\s\-/.]*(?:of\s+)?{_MON}\b[,\s\-/.]*(\d{{4}})?", re.I)),
    ("mdy_name", re.compile(rf"\b{_MON}\s+{_DAY}\b,?\s*(\d{{4}})?", re.I)),
    ("iso", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")),
    ("dmy_num", re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b")),
    ("relative", re.compile(r"\b(today|tomorrow|day after tomorrow)\b", re.I)),
]
TIME = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?|hrs|hours)\b"
                  r"|\b([01]?\d|2[0-3]):([0-5]\d)\b", re.I)


@dataclass
class FoundEvent:
    title: str
    start: str                 # ISO 8601; date only when all_day
    end: str | None
    all_day: bool
    category: str
    location: str
    source: str                # Gmail message id — used to avoid adding it twice
    source_from: str
    source_subject: str
    evidence: str              # the text the date was found in, for the preview

    def as_dict(self) -> dict:
        """The event as a plain dict, so it can pass between plan steps."""
        return asdict(self)

    def describe(self) -> str:
        """One readable line for the preview, e.g. 'Mid-term exam — Tue 6 Oct 2026, 10:00 AM (from
        Exam Cell)'.
        """
        when = _human_when(self.start, self.all_day)
        who = self.source_from.split("<")[0].strip(' "') or self.source_from
        return f"{self.title} — {when}   (from {who})"


def find_events(mails: list[dict], now: datetime | None = None) -> list[FoundEvent]:
    """Find upcoming dated events in a list of emails: booking data first, text rules otherwise. At
    most 3 per email and 12 in total, sorted by date, duplicates removed.
    """
    now = now or datetime.now().astimezone()
    out: list[FoundEvent] = []
    seen: set[tuple] = set()
    for mail in mails:
        found = _structured(mail, now) or _from_text(mail, now)
        for ev in found[:MAX_PER_MAIL]:
            key = (ev.start[:16], ev.category, ev.source)
            if key not in seen:
                seen.add(key)
                out.append(ev)
    out.sort(key=lambda e: e.start)
    return out[:MAX_TOTAL]


# --------------------------------------------------------------------------- #
# 1. schema.org reservations
# --------------------------------------------------------------------------- #


def _structured(mail: dict, now: datetime) -> list[FoundEvent]:
    """Read flight, train, bus and event bookings from the schema.org data airlines and booking
    sites embed in their emails.
    """
    out = []
    for item in mail.get("structured", []) or []:
        kind = str(item.get("@type", ""))
        res = item.get("reservationFor") or {}
        if kind == "FlightReservation":
            airline = (res.get("airline") or {}).get("name", "") or ""
            num = f"{(res.get('airline') or {}).get('iataCode', '')}{res.get('flightNumber', '')}"
            dep = res.get("departureAirport") or {}
            arr = res.get("arrivalAirport") or {}
            title = (f"Flight {num or airline} {dep.get('iataCode', '')}→"
                     f"{arr.get('iataCode', '')}").strip()
            start, end = res.get("departureTime"), res.get("arrivalTime")
            cat, loc = "Flight", dep.get("name", "") or dep.get("iataCode", "")
        elif kind in ("TrainReservation", "BusReservation"):
            cat = "Train" if kind == "TrainReservation" else "Bus"
            dep = res.get("departureStation") or res.get("departureBusStop") or {}
            arr = res.get("arrivalStation") or res.get("arrivalBusStop") or {}
            title = (f"{cat} {res.get('trainNumber', '') or res.get('busNumber', '')} "
                     f"{dep.get('name', '')} → {arr.get('name', '')}").strip()
            start, end = res.get("departureTime"), res.get("arrivalTime")
            loc = dep.get("name", "")
        elif kind == "EventReservation":
            title = res.get("name", "Event")
            start, end = res.get("startDate"), res.get("endDate")
            cat, loc = "Event", (res.get("location") or {}).get("name", "")
        else:
            continue
        s = _parse_iso(start)
        if s is None or s < now:
            continue
        e = _parse_iso(end) if end else None
        pnr = item.get("reservationNumber")
        out.append(FoundEvent(
            title=_clip(title + (f" (PNR {pnr})" if pnr else "")), start=s.isoformat(),
            end=e.isoformat() if e and e > s else None, all_day=False, category=cat,
            location=_clip(loc, 120), source=str(mail.get("id", "")),
            source_from=mail.get("from", ""), source_subject=mail.get("subject", ""),
            evidence="booking details embedded in the email"))
    return out


def _parse_iso(s) -> datetime | None:
    """Parse an ISO date-time, assuming local time when no time zone is given; None if it cannot be
    read.
    """
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


# --------------------------------------------------------------------------- #
# 2. text rules
# --------------------------------------------------------------------------- #


def _from_text(mail: dict, now: datetime) -> list[FoundEvent]:
    """Find events in the email text: a date (and time, if near it) close to a keyword that says
    what it is (exam, flight, train, deadline...). Past dates are skipped.
    """
    subject = mail.get("subject", "") or ""
    body = mail.get("body", "") or ""
    text = f"{subject}\n{body}"
    sent = _sent_date(mail.get("date", ""), now)
    subject_cat = _category(subject)
    out = []
    for m_start, m_end, day in _dates(text, sent):
        window = text[max(0, m_start - LOOKAROUND): m_end + LOOKAROUND]
        cat = _category(window) or subject_cat
        if cat is None:
            continue
        t = _time_near(text, m_start, m_end)
        if t:
            start = datetime.combine(day, t).astimezone()
            if start < now:
                continue
            end = start + timedelta(hours=2 if cat in ("Flight", "Train", "Exam") else 1)
            s_iso, e_iso, all_day = start.isoformat(), end.isoformat(), False
        else:
            if day < now.date():
                continue
            s_iso, e_iso, all_day = day.isoformat(), None, True
        pnr = PNR.search(text)
        title = _title(cat, subject, pnr.group(1) if pnr and cat in ("Flight", "Train",
                                                                       "Bus") else None)
        snippet = re.sub(r"\s+", " ", text[max(0, m_start - 60): m_end + 60]).strip()
        out.append(FoundEvent(
            title=title, start=s_iso, end=e_iso, all_day=all_day, category=cat,
            location="", source=str(mail.get("id", "")), source_from=mail.get("from", ""),
            source_subject=subject, evidence=_clip(snippet, 160)))
    return out


def _category(text: str) -> str | None:
    """The category with the most keyword hits; ties go to the earlier one.
    First-match was wrong for an IRCTC mail that also says "departure"."""
    best, best_n = None, 0
    for name, rx in _CAT:
        n = len(rx.findall(text))
        if n > best_n:
            best, best_n = name, n
    return best


def _title(cat: str, subject: str, pnr: str | None) -> str:
    """Build the event title from the category and email subject, adding the PNR for tickets."""
    subj = re.sub(r"^\s*(re|fwd?|fw)\s*:\s*", "", subject, flags=re.I).strip()
    base = subj if subj and cat.lower() in subj.lower() else f"{cat}: {subj or 'from email'}"
    if pnr and pnr in base:
        pnr = None
    return _clip(base + (f" (PNR {pnr})" if pnr else ""))


def _sent_date(header: str, now: datetime) -> date:
    """The date the email was sent, used to resolve 'tomorrow' and dates with no year."""
    try:
        return parsedate_to_datetime(header).astimezone().date()
    except (TypeError, ValueError, IndexError):
        return now.date()


def _dates(text: str, sent: date):
    """Yield (start, end, date) for every date mention, earliest-first."""
    hits: list[tuple[int, int, date]] = []
    taken: list[tuple[int, int]] = []
    for kind, rx in DATE_PATTERNS:
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            d = _to_date(kind, m, sent)
            if d is not None:
                hits.append((m.start(), m.end(), d))
                taken.append((m.start(), m.end()))
    return sorted(hits)


def _to_date(kind: str, m: re.Match, sent: date) -> date | None:
    """Turn one matched date pattern into a real date, choosing the next occurrence when no year is
    written.
    """
    try:
        if kind == "dmy_name":
            day, mon, year = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], m.group(3)
        elif kind == "mdy_name":
            mon, day, year = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), m.group(3)
        elif kind == "iso":
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif kind == "dmy_num":
            day, mon, y = int(m.group(1)), int(m.group(2)), m.group(3)
            year = str(2000 + int(y)) if len(y) == 2 else y
        else:
            word = m.group(1).lower()
            return sent + timedelta(days={"today": 0, "tomorrow": 1}.get(word, 2))
        if year:
            return date(int(year), mon, day)
        d = date(sent.year, mon, day)          # no year: the next such date
        return d if d >= sent else date(sent.year + 1, mon, day)
    except (ValueError, KeyError):
        return None


def _time_near(text: str, start: int, end: int):
    """A time written within ~50 characters of the date, e.g. 'at 10:30 AM'."""
    from datetime import time

    window_after = text[end: end + 50]
    window_before = text[max(0, start - 40): start]
    for w in (window_after, window_before):
        m = TIME.search(w)
        if not m:
            continue
        if m.group(4):                                      # 24-hour hh:mm
            return time(int(m.group(4)), int(m.group(5)))
        h, mins, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
        if suffix.startswith("p") and h < 12:
            h += 12
        if suffix.startswith("a") and h == 12:
            h = 0
        if 0 <= h < 24 and 0 <= mins < 60:
            return time(h, mins)
    return None


def _human_when(start: str, all_day: bool) -> str:
    """Format a start time for people, e.g. 'Tue 06 Oct 2026, 10:00 AM' or '(all day)'."""
    if all_day:
        d = date.fromisoformat(start[:10])
        return d.strftime("%a %d %b %Y") + " (all day)"
    d = datetime.fromisoformat(start)
    return d.strftime("%a %d %b %Y, %I:%M %p").replace(" 0", " ")


def _clip(s: str, n: int = 100) -> str:
    """Collapse whitespace and shorten text to n characters."""
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


