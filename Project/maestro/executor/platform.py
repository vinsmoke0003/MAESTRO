"""The ONE place in MAESTRO that is allowed to know which OS it is running on.

docs/02-ARCHITECTURE.md §7: platform-specific code exists only under
`maestro/executor/{darwin,win32}/`, and nothing above L1 may branch on the
platform. `scripts/check_platform_boundary.py` enforces that by grepping for
`sys.platform` / `platform.system()` outside this package — this module is the
single allowed exception, and it exists so the exception is exactly one file.

Backends are resolved lazily so that importing MAESTRO never imports pywinauto
on a Mac or pyobjc on Windows.
"""

from __future__ import annotations

import sys
from types import ModuleType


def platform_key() -> str:
    """darwin | win32 | other."""
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "other"


def backend(name: str) -> ModuleType:
    """Import `maestro.executor.<platform>.<name>`, falling back to the portable
    stub backend on unsupported platforms.

    The stub raises `NotAvailable` from every call rather than pretending to
    succeed — a silent no-op is exactly the T8 silent-failure mode the safety
    spec is built to prevent.
    """
    import importlib

    key = platform_key()
    for pkg in (key, "portable"):
        try:
            return importlib.import_module(f"maestro.executor.{pkg}.{name}")
        except ModuleNotFoundError:
            continue
    raise ImportError(f"no backend {name!r} for platform {key}")
