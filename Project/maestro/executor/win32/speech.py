"""Windows text-to-speech: the SAPI voice that ships with Windows.

PowerShell reads the sentence from standard input, so the text is data and is
never spliced into the script — the same rule the app backend follows for
application names.
"""

from __future__ import annotations

import shutil
import subprocess

from maestro.executor.base import NotAvailable

_SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "if ($args.Count -gt 0) { $s.Rate = [int]$args[0] }; "
    "$s.Speak([Console]::In.ReadToEnd())"
)


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def available() -> bool:
    return _powershell() is not None


def speak(text: str, rate: int | None = None) -> None:
    exe = _powershell()
    if exe is None:
        raise NotAvailable("PowerShell was not found; Windows speech is unavailable")
    # SAPI rate is -10..10; map words-per-minute (~175 normal) onto it.
    extra = [str(max(-10, min(10, round((rate - 175) / 25))))] if rate else []
    proc = subprocess.run(  # noqa: S603
        [exe, "-NoProfile", "-NonInteractive", "-Command", _SCRIPT, *extra],
        input=text, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise NotAvailable(f"Windows speech failed: {proc.stderr.strip()[:200]}")
