"""Mouth: MAESTRO's spoken replies.

Text-to-speech uses what the operating system already ships — `say` on macOS,
the built-in SAPI voice on Windows, `espeak` on Linux — so speaking costs
nothing and needs no model download. Choosing between them is a platform
decision, so it lives behind `maestro.executor.platform.backend("speech")`;
this module never looks at the OS itself (docs/02 §7).

What is spoken is always a short, cleaned version of what is printed. The full
preview stays on screen: a list of 40 file paths read aloud is not consent,
it is noise.
"""

from __future__ import annotations

import re
from typing import Protocol

from maestro.executor.base import NotAvailable


class Mouth(Protocol):
    def say(self, text: str) -> None: ...


class SystemMouth:
    """Speaks through the OS voice; degrades to silence if there is none."""

    def __init__(self, *, rate: int | None = None):
        from maestro.executor.platform import backend

        self._speech = backend("speech")
        self.rate = rate
        self.available = True

    def say(self, text: str) -> None:
        text = speakable(text)
        if not text or not self.available:
            return
        try:
            self._speech.speak(text, rate=self.rate)
        except NotAvailable:
            self.available = False   # printed output still carries everything


class SilentMouth:
    """`--quiet`, and the fallback when no system voice exists."""

    def say(self, text: str) -> None:
        return None


class RecordingMouth:
    """Tests: remembers everything it was asked to say."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(speakable(text))

    @property
    def transcript(self) -> str:
        return " | ".join(self.said)


_PATH = re.compile(r"(?:[A-Za-z]:)?[~/\\][^\s,;]+")


def speakable(text: str, limit: int = 320) -> str:
    """Shorten and de-symbol text so a speech engine reads it naturally."""
    t = text.replace("—", ", ").replace("->", " to ").replace("→", " to ")
    t = _PATH.sub(lambda m: _short_path(m.group(0)), t)
    t = re.sub(r"\(s\)", "s", t)
    t = re.sub(r"[`*_#>|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        cut = t[:limit].rsplit(". ", 1)[0]
        t = (cut if len(cut) > limit // 2 else t[:limit]).rstrip(" ,.") + "."
    return t


def _short_path(p: str) -> str:
    """'~/Documents/Invoices/2024' -> 'Invoices 2024'-ish: the last two parts."""
    parts = [x for x in re.split(r"[/\\]", p) if x and x not in ("~", ".")]
    return " ".join(parts[-2:]) if parts else p
