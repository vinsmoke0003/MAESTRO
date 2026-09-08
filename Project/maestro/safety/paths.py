"""Path policy: allowlist + denylist with canonicalization.

Order matters and is load-bearing (docs/06-SAFETY-SPEC.md §2):

  1. expand ~ / env vars and resolve the path (realpath) BEFORE any matching —
     otherwise `~/Downloads/../../.ssh/id_rsa` sails through the allowlist,
     and a symlink inside an allowed directory can point anywhere.
  2. denylist is checked first and wins.
  3. anything not under the allowlist is OUTSIDE (escalates risk, docs/06),
     and unparseable paths fail closed as DENIED.

The deny list is the union of the macOS and Windows sensitive locations. It is
deliberately *not* branched on the platform: a Windows path is nonsense on
macOS and simply never matches, and keeping one list means the cross-platform
differential test (docs/02 §7) compares identical policy on both machines.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePath


class PathVerdict(Enum):
    ALLOWED = "allowed"  # canonical path under an allowlist root
    OUTSIDE = "outside"  # legal, but not in the declared workspace -> R2+
    DENIED = "denied"  # denylisted or unparseable -> BLOCKED


DEFAULT_ALLOW = [
    "~/Desktop",
    "~/Documents",
    "~/Downloads",
    "~/Pictures",
    "~/Music",
    "~/Videos",
    "~/maestro_workspace",
]

# Directories no plan may touch. Union of macOS + Windows + POSIX locations.
DEFAULT_DENY_DIRS = [
    # cross-platform user secrets
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config",
    "~/.kube",
    "~/.docker",
    # macOS
    "~/Library/Keychains",
    "~/Library/Application Support",
    "/System",
    "/Library",
    "/private",
    # POSIX
    "/etc",
    "/var",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/root",
    # Windows
    "C:/Windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
    "~/AppData",
    "~/.aws",
]

# Filename patterns that are sensitive wherever they live.
DEFAULT_DENY_PATTERNS = [
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    ".env",
    "*.env",
    "*.kdbx",
    "*wallet*",
    "credentials",
    "*.ovpn",
    "shadow",
    "sam",
    "ntuser.dat",
    "*.sqlite-credentials",
]


def default_allow_roots() -> list[str]:
    """The standard roots plus wherever MAESTRO_WORKSPACE actually points.

    Read at construction, not at import: the eval harness relocates the
    workspace per run so that every benchmark task is hermetic.
    """
    from maestro.config import settings

    roots = list(DEFAULT_ALLOW)
    ws = str(settings().workspace)
    if ws not in roots:
        roots.append(ws)
    return roots


@dataclass
class PathPolicy:
    allow_roots: list[str] = field(default_factory=default_allow_roots)
    deny_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_DIRS))
    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_PATTERNS))

    def __post_init__(self) -> None:
        # Canonicalise the policy once, not per check: the policy roots do not
        # move, and `check()` is called thousands of times by the harness.
        self._deny_dirs_c = [self._canon(d) for d in self.deny_dirs]
        self._allow_roots_c = [self._canon(a) for a in self.allow_roots]

    # -- canonicalisation --------------------------------------------------

    @staticmethod
    def _canon(p: str | Path) -> Path:
        # strict=False: the path may not exist yet (e.g. a mkdir target).
        # resolve() still folds `..` and resolves existing symlink prefixes.
        s = os.path.expandvars(str(p))
        return Path(s).expanduser().resolve(strict=False)

    def canonical(self, p: str | Path) -> Path:
        return self._canon(p)

    # -- the decision ------------------------------------------------------

    def check(self, p: str | Path) -> PathVerdict:
        try:
            cp = self._canon(p)
        except (OSError, RuntimeError, ValueError):
            return PathVerdict.DENIED  # fail closed

        # A raw string containing a NUL or a device name is not a path we will
        # reason about. Fail closed rather than guess.
        raw = str(p)
        if "\x00" in raw:
            return PathVerdict.DENIED

        # Denylist first; it always wins.
        for dd in self._deny_dirs_c:
            if cp == dd or _is_relative_to(cp, dd):
                return PathVerdict.DENIED
        name = cp.name.lower()
        for pat in self.deny_patterns:
            if fnmatch.fnmatch(name, pat):
                return PathVerdict.DENIED
        # Any *component* of the path being a denied directory name catches
        # `~/Documents/.ssh/backup/id_rsa` too.
        lowered = {c.lower() for c in cp.parts}
        if lowered & {".ssh", ".aws", ".gnupg", "keychains"}:
            return PathVerdict.DENIED

        for aa in self._allow_roots_c:
            if cp == aa or _is_relative_to(cp, aa):
                return PathVerdict.ALLOWED

        return PathVerdict.OUTSIDE

    def explain(self, p: str | Path) -> str:
        v = self.check(p)
        if v is PathVerdict.DENIED:
            return f"{p} is on the denylist (or unresolvable) — no override exists"
        if v is PathVerdict.OUTSIDE:
            return f"{p} is outside the declared workspace roots — requires consent"
        return f"{p} is inside the declared workspace"

    def with_workspace(self, extra: str | Path) -> PathPolicy:
        """Return a copy that also allows `extra` (used by the eval harness to
        point MAESTRO at a hermetic fixture directory)."""
        return PathPolicy(
            allow_roots=[*self.allow_roots, str(extra)],
            deny_dirs=list(self.deny_dirs),
            deny_patterns=list(self.deny_patterns),
        )


def _is_relative_to(child: PurePath, parent: PurePath) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
