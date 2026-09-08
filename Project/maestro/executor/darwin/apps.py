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
    return KNOWN_APPS.get(app_id.strip().lower(), app_id.strip())


def launch(app_id: str) -> str:
    name = _resolve(app_id)
    proc = subprocess.run(["open", "-a", name], capture_output=True, text=True)  # noqa: S603,S607
    if proc.returncode != 0:
        raise NotAvailable(f"could not launch {app_id!r}: {proc.stderr.strip()}")
    return f"launched {name}"


def quit(app_id: str, force: bool = False) -> str:  # noqa: A001 - mirrors the verb name
    name = _resolve(app_id)
    script = _FORCE_SCRIPT if force else _QUIT_SCRIPT
    proc = subprocess.run(  # noqa: S603
        ["osascript", "-e", script, name], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise NotAvailable(f"could not quit {app_id!r}: {proc.stderr.strip()}")
    return f"quit {name}"
