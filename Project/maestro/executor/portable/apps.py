"""Fallback app backend for platforms MAESTRO does not support (e.g. Linux CI).

Every call raises NotAvailable. It must never pretend to succeed: a silent
no-op is the T8 silent-failure mode the whole safety spec exists to prevent.
"""

from __future__ import annotations

from maestro.executor.base import NotAvailable

KNOWN_APPS: dict[str, str] = {}


def launch(app_id: str) -> str:
    raise NotAvailable(f"app.launch is not implemented on this platform ({app_id!r})")


def quit(app_id: str, force: bool = False) -> str:  # noqa: A001
    raise NotAvailable(f"app.quit is not implemented on this platform ({app_id!r})")
