"""What goes into MAESTRO.exe's bundle, and what must never go in.

Plain Python with no PyInstaller import, so `maestro.spec` can use it at build
time and the test suite can check it on any machine.

MAESTRO's own files are listed explicitly (not globbed): the page and the
trained intent classifier. Everything else is code PyInstaller finds itself or
third-party package data. `check_datas()` is run on the FINAL list the spec is
about to bundle and fails the build if any entry looks like a credential, a
token, a database, an environment file, a downloaded speech model, or anything
from the user's home or MAESTRO's state folder.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

# File names that must never be bundled, wherever they come from.
# (Public CA bundles such as certifi's cacert.pem are allowed: HTTPS to Google needs them.)
FORBIDDEN_NAMES = (
    "client_secret*.json", "token.json", "token_*.json", "credentials*.json",
    "*.db", "*.sqlite", "*.sqlite3", "*.db-journal", "*.log",
    ".env", ".env.*", "*.key", "id_rsa*",
    # downloaded / converted speech and language models
    "model.bin", "*.gguf", "*.safetensors", "*.pt", "*.ckpt",
)
# Directory names that hold user data or downloaded model caches. (Tests, eval and
# training code are kept out by the spec's module excludes, not here: third-party
# package data legitimately lives under folders such as .venv.)
FORBIDDEN_DIRS = (".maestro", "maestro_workspace", ".cache", "huggingface")


def bundle_datas(root: Path) -> list[tuple[str, str]]:
    """MAESTRO's own non-Python files: (source, folder inside the bundle).

    The folders mirror how the code finds them: `ui.py` reads `ui.html` next to
    itself, and `config.intent_model` looks two levels above `config.py`.
    """
    root = Path(root).resolve()
    return [
        (str(root / "maestro" / "ui.html"), "maestro"),
        (str(root / "models" / "intent" / "intent_clf.joblib"), "models/intent"),
    ]


def forbidden_reason(source: str, home: Path | None = None) -> str | None:
    """Why `source` must not be bundled, or None if it is acceptable."""
    p = Path(source)
    name = p.name.lower()
    for pattern in FORBIDDEN_NAMES:
        if fnmatch.fnmatch(name, pattern.lower()):
            return f"file name matches {pattern!r}"
    parts = {part.lower() for part in p.parts[:-1]}
    for d in FORBIDDEN_DIRS:
        if d.lower() in parts:
            return f"inside a {d!r} folder"
    if home is not None:
        h = Path(home).resolve()
        for state in (h / ".maestro", h / "maestro_workspace", h / ".cache"):
            try:
                p.resolve().relative_to(state)
                return "inside the user's MAESTRO state, workspace or cache"
            except ValueError:
                pass
    return None


def check_datas(entries, home: Path | None = None) -> None:
    """Fail (SystemExit) if any (source, dest[, kind]) entry is forbidden."""
    bad = []
    for entry in entries:
        source = entry[1] if len(entry) == 3 else entry[0]   # TOC tuples are (dest, src, kind)
        reason = forbidden_reason(str(source), home)
        if reason:
            bad.append(f"  {source}: {reason}")
    if bad:
        raise SystemExit("Refusing to bundle these files:\n" + "\n".join(bad))
