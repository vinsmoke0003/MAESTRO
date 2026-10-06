"""macOS text-to-speech: the built-in `say` command.

The text is written to `say`'s standard input rather than passed as an
argument, so a sentence that happens to start with "-" can never be read as a
command-line option. Nothing is interpolated into a shell.
"""

from __future__ import annotations

import shutil
import subprocess

from maestro.executor.base import NotAvailable


def available() -> bool:
    """True if the macOS `say` command exists."""
    return shutil.which("say") is not None


def speak(text: str, rate: int | None = None) -> None:
    """Say the text aloud with `say`, passing it on standard input so it can never be read as a
    command option.
    """
    if not available():
        raise NotAvailable("the macOS `say` command was not found")
    cmd = ["say"] + (["-r", str(int(rate))] if rate else [])
    proc = subprocess.run(cmd, input=text, text=True, capture_output=True)  # noqa: S603
    if proc.returncode != 0:
        raise NotAvailable(f"say failed: {proc.stderr.strip()}")
