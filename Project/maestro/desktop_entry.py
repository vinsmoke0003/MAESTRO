"""Entry point for the packaged desktop app (MAESTRO.exe, built windowed).

It only calls `maestro.desktop.run()`: the same local server, Workspace,
pipeline and consent workflow as `maestro desktop`. Nothing else starts here.

A windowed build has no console, so `sys.stdout` / `sys.stderr` are None.
Libraries that write progress output (the Whisper model download, for one)
would crash on that, so both are pointed at the null device first. If startup
fails, the user gets a short, fixed message: never an exception text, a path or
a credential. Run `maestro desktop` from a terminal to see details.
"""

from __future__ import annotations

import os
import sys

FAILED = ("MAESTRO could not start.\n\n"
          "Run `maestro desktop` from a terminal to see why, or use `maestro ui` "
          "to open MAESTRO in your browser.")


def _ensure_streams() -> None:
    """Give a console-less process real (discarding) stdout and stderr."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def _show_error(message: str) -> None:
    """Show `message` in a native dialog where one is available (Windows); otherwise print
    it. Uses capability detection, not an OS check, and never raises.
    """
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "MAESTRO", 0x10)  # MB_ICONERROR
        return
    except Exception:  # noqa: BLE001 - no native dialog here
        pass
    try:
        print(message, file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    """Start the desktop app; return its exit status."""
    _ensure_streams()
    try:
        from maestro import desktop

        status = desktop.run()
    except Exception:  # noqa: BLE001 - nothing internal reaches the user
        status = 1
    if status != 0:
        _show_error(FAILED)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
