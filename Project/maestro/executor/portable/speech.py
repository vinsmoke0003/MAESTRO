"""Fallback text-to-speech for other platforms: espeak-ng / espeak if present.

If neither exists, `speak` raises NotAvailable and the voice agent carries on
with printed replies only. It never pretends to have spoken.
"""

from __future__ import annotations

import shutil
import subprocess

from maestro.executor.base import NotAvailable


def _engine() -> str | None:
    """Path to espeak-ng or espeak, or None."""
    return shutil.which("espeak-ng") or shutil.which("espeak")


def available() -> bool:
    """True if a speech engine is installed."""
    return _engine() is not None


def speak(text: str, rate: int | None = None) -> None:
    """Say the text with espeak, sending it on standard input."""
    exe = _engine()
    if exe is None:
        raise NotAvailable("no speech engine found (install espeak-ng)")
    cmd = [exe, "--stdin"] + (["-s", str(int(rate))] if rate else [])
    proc = subprocess.run(cmd, input=text, text=True, capture_output=True)  # noqa: S603
    if proc.returncode != 0:
        raise NotAvailable(f"espeak failed: {proc.stderr.strip()}")
