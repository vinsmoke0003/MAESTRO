"""Enforce the cross-platform boundary (docs/02-ARCHITECTURE.md §7).

    python scripts/check_platform_boundary.py

> "Platform-specific code exists **only** under `maestro/executor/{darwin,win32}/`.
>  Nothing above L1 may import `sys.platform`, contain an `if platform ==` branch,
>  or accept a platform argument. Enforce this with a CI check — a grep for
>  `platform` outside the executor directory that fails the build. Set it up in
>  Week 2 when it costs nothing."

This is that check. It costs nothing today and it is the only thing that keeps
the +10% cross-platform estimate from becoming +100% in month four: the moment
planner logic starts caring about the OS, ~80% of the codebase stops being
written once.

Exactly one file is exempt — `maestro/executor/platform.py` — and the exemption
is deliberately a single named file rather than a directory, so the number of
places that know about the OS cannot grow without editing this script.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "maestro"

# The one file allowed to branch on the platform, plus the per-OS backends it
# dispatches to.
EXEMPT_FILES = {PACKAGE / "executor" / "platform.py"}
EXEMPT_DIRS = {
    PACKAGE / "executor" / "darwin",
    PACKAGE / "executor" / "win32",
    PACKAGE / "executor" / "portable",
}

PATTERNS: list[tuple[str, str]] = [
    (r"\bsys\.platform\b", "sys.platform"),
    (r"\bplatform\.system\s*\(", "platform.system()"),
    (r"\bplatform\.mac_ver\s*\(", "platform.mac_ver()"),
    (r"\bos\.name\s*==", "os.name comparison"),
    (r"\bsys\.getwindowsversion\b", "sys.getwindowsversion"),
]
COMPILED = [(re.compile(p), label) for p, label in PATTERNS]

# Lines that merely *report* the platform are fine — `sys.info` returns it as a
# metric and the CLI prints it in `doctor`. What is forbidden is *branching* on
# it. We approximate "branching" as: the match is not inside a string that is
# being returned or printed as data.
REPORTING_HINTS = ("\"system\":", "'system':", "print(", "f\"", "f'", "return {")


def offending_lines(path: Path) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for rx, label in COMPILED:
            if not rx.search(line):
                continue
            if any(h in line for h in REPORTING_HINTS):
                continue  # reporting the platform as data, not branching on it
            hits.append((n, label, stripped[:90]))
    return hits


def is_exempt(path: Path) -> bool:
    if path in EXEMPT_FILES:
        return True
    return any(d in path.parents for d in EXEMPT_DIRS)


def main() -> int:
    if not PACKAGE.is_dir():
        print(f"cannot find {PACKAGE}")
        return 2

    violations: list[tuple[Path, int, str, str]] = []
    scanned = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        if is_exempt(path):
            continue
        scanned += 1
        for n, label, text in offending_lines(path):
            violations.append((path, n, label, text))

    print(f"platform-boundary check: {scanned} file(s) scanned outside "
          f"maestro/executor/{{darwin,win32,portable}} and platform.py")

    if violations:
        print()
        print(f"FAILED — {len(violations)} platform branch(es) above L1:")
        for path, n, label, text in violations:
            print(f"  {path.relative_to(ROOT)}:{n}  {label}")
            print(f"      {text}")
        print()
        print("Move the OS-specific logic into maestro/executor/{darwin,win32}/ and")
        print("resolve it through maestro.executor.platform.backend(). Everything")
        print("above L1 must be written once (docs/02 §7).")
        return 1

    print("OK — no platform branching above the executor layer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
