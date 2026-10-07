"""macOS application backend: `open -a` to launch, AppleScript to quit.

`osascript` is invoked with an argument list, never a formatted shell string,
and the application name is passed as a *parameter* to the script rather than
interpolated into it — so an app name is data on macOS for the same reason it
is data on Windows.
"""

from __future__ import annotations

import subprocess

from maestro.executor.base import NotAvailable

# Friendly name -> macOS application name
KNOWN_APPS: dict[str, str] = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "safari": "Safari",
    "firefox": "Firefox",
    "edge": "Microsoft Edge",
    "finder": "Finder",
    "notes": "Notes",
    "calculator": "Calculator",
    "terminal": "Terminal",
    "iterm": "iTerm",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "spotify": "Spotify",
    "mail": "Mail",
    "preview": "Preview",
    "textedit": "TextEdit",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
}

_QUIT_SCRIPT = 'on run argv\n  tell application (item 1 of argv) to quit\nend run'
_FORCE_SCRIPT = ('on run argv\n  tell application "System Events" to '
                 'quit application (item 1 of argv)\nend run')


def _resolve(app_id: str) -> str:
    """Map a friendly name like 'chrome' to the macOS application name 'Google Chrome'."""
    return KNOWN_APPS.get(app_id.strip().lower(), app_id.strip())


# `open` and AppleScript can wait forever for a permission prompt nobody can answer
# (a headless CI Mac, a locked session). A bounded wait turns that into an error.
TIMEOUT_S = 15


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    """Run a short macOS command with a time limit; NotAvailable if it does not finish."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,  # noqa: S603
                              timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise NotAvailable(f"{what} did not respond within {TIMEOUT_S} s") from None


def launch(app_id: str) -> str:
    """Open an application with `open -a`. Raises NotAvailable if macOS cannot find it."""
    name = _resolve(app_id)
    proc = _run(["open", "-a", name], f"launching {app_id!r}")
    if proc.returncode != 0:
        raise NotAvailable(f"could not launch {app_id!r}: {proc.stderr.strip()}")
    return f"launched {name}"


def quit(app_id: str, force: bool = False) -> str:  # noqa: A001 - mirrors the verb name
    """Ask an application to quit via AppleScript (or force it). The name is passed as data, never
    spliced into the script.
    """
    name = _resolve(app_id)
    script = _FORCE_SCRIPT if force else _QUIT_SCRIPT
    proc = _run(["osascript", "-e", script, name], f"quitting {app_id!r}")
    if proc.returncode != 0:
        raise NotAvailable(f"could not quit {app_id!r}: {proc.stderr.strip()}")
    return f"quit {name}"
