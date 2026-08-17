"""Path policy: allowlist + denylist with canonicalization.

Order matters and is load-bearing (docs/06-SAFETY-SPEC.md §2):

  1. expand ~ and resolve the path (realpath) BEFORE any matching —
     otherwise `~/Downloads/../../.ssh/id_rsa` sails through the allowlist,
     and a symlink inside an allowed directory can point anywhere.
  2. denylist is checked first and wins.
  3. anything not under the allowlist is OUTSIDE (escalates risk, docs/06),
     and unparseable paths fail closed as DENIED.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class PathVerdict(Enum):
    ALLOWED = "allowed"  # canonical path under an allowlist root
    OUTSIDE = "outside"  # legal, but not in the declared workspace -> R2+
    DENIED = "denied"  # denylisted or unparseable -> BLOCKED


DEFAULT_ALLOW = [
    "~/Desktop",
    "~/Documents",
    "~/Downloads",
    "~/Pictures",
    "~/maestro_workspace",
]

# Directories that no plan may touch, plus filename patterns that are
# sensitive wherever they live.
DEFAULT_DENY_DIRS = [
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config",
    "~/Library/Keychains",
    "/System",
    "/Library",
    "/etc",
    "/var",
    "/usr",
    "/bin",
    "/sbin",
]
DEFAULT_DENY_PATTERNS = [
    "*.key",
    "*.pem",
    "id_rsa*",
    "id_ed25519*",
    ".env",
    "*.kdbx",
    "*wallet*",
]


@dataclass
class PathPolicy:
    allow_roots: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOW))
    deny_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_DIRS))
    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_PATTERNS))

    def _canon(self, p: str | Path) -> Path:
        # strict=False: the path may not exist yet (e.g. a mkdir target).
        # resolve() still folds `..` and resolves existing symlink prefixes.
        return Path(p).expanduser().resolve(strict=False)

    def check(self, p: str | Path) -> PathVerdict:
        try:
            cp = self._canon(p)
        except (OSError, RuntimeError, ValueError):
            return PathVerdict.DENIED  # fail closed

        # Denylist first; it always wins.
        for d in self.deny_dirs:
            dd = self._canon(d)
            if cp == dd or dd in cp.parents:
                return PathVerdict.DENIED
        name = cp.name.lower()
        for pat in self.deny_patterns:
            if fnmatch.fnmatch(name, pat):
                return PathVerdict.DENIED

        for a in self.allow_roots:
            aa = self._canon(a)
            if cp == aa or aa in cp.parents:
                return PathVerdict.ALLOWED

        return PathVerdict.OUTSIDE
