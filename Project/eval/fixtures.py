"""Hermetic fixtures for the benchmark (docs/07 §3).

> "Every task must be hermetic: a setup script builds a fixture directory from
>  scratch, and teardown removes it. No task may depend on the state left by
>  another. This is the difference between a benchmark you can re-run 500 times
>  and one that silently degrades after the first pass."

`sandbox()` is that guarantee. Inside it:

* `MAESTRO_HOME` and `MAESTRO_WORKSPACE` point into a fresh temp directory, so
  the audit log, episode store and preferences of a benchmark run never touch
  the user's real ones;
* the folder gazetteer is redirected — "Downloads", "Desktop", "Documents",
  "Pictures" resolve to directories *inside the sandbox*. The benchmark can
  therefore use natural instructions ("move the PDFs from Downloads to
  Documents/Invoices") without ever reading or writing the real Downloads
  folder;
* the path policy allows the sandbox root and nothing else outside it, so a
  task that escapes is a *failure the harness detects*, not a mess on the disk;
* everything is deleted on exit.

The gazetteer redirect is a test-harness concern and lives only here. Nothing in
`maestro/` knows the sandbox exists.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from maestro.nlp import entities as _entities
from maestro.safety import PathPolicy
from maestro.safety.paths import DEFAULT_DENY_DIRS


def _is_ancestor_of(candidate: Path, target: Path) -> bool:
    try:
        target.relative_to(candidate)
        return True
    except ValueError:
        return False

# The real-world folder names benchmark instructions are allowed to mention.
# Each becomes a directory inside the sandbox.
SANDBOX_FOLDERS: dict[str, str] = {
    "downloads": "Downloads",
    "download folder": "Downloads",
    "desktop": "Desktop",
    "documents": "Documents",
    "docs folder": "Documents",
    "pictures": "Pictures",
    "photos": "Pictures",
    "music": "Music",
    "videos": "Videos",
    "workspace": "workspace",
    "maestro workspace": "workspace",
    "inbox": "workspace/inbox",
    "archive": "workspace/archive",
    "invoices": "Documents/Invoices",
    "finance": "Documents/Finance",
    "finance folder": "Documents/Finance",
    "receipts": "Documents/Receipts",
    "reports": "Documents/Reports",
    "screenshots": "Pictures/Screenshots",
    "semester": "Documents/Semester",
    "assignments": "Documents/Assignments",
    "notes": "Documents/Notes",
    "projects": "Documents/Projects",
}

BASE_DIRS = ["Downloads", "Desktop", "Documents", "Pictures", "Music", "Videos",
             "workspace", "workspace/inbox", "workspace/archive"]


@dataclass
class Sandbox:
    root: Path
    home: Path
    workspace: Path
    policy: PathPolicy
    known_paths: dict[str, str] = field(default_factory=dict)

    def path(self, rel: str) -> Path:
        """Resolve a fixture-relative path ('Downloads/a.pdf') inside the sandbox."""
        return self.root / str(rel).replace("\\", "/").lstrip("/")

    def file_count(self) -> int:
        return sum(1 for p in self.root.rglob("*") if p.is_file())

    def snapshot(self) -> dict[str, str]:
        """path -> sha256, for the 'nothing changed' and 'undo restored it'
        predicates.

        Sizes were the first version, and sizes cannot tell a restored file from
        a same-length one with different bytes — so "undo reliability" would have
        passed a move that put the wrong content back. Hashing costs nothing at
        fixture scale and turns the URR number into a claim about contents.
        """
        import hashlib

        out: dict[str, str] = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                key = str(p.relative_to(self.root)).replace("\\", "/")
                out[key] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out


@contextlib.contextmanager
def sandbox(fixture: dict | None = None, keep: bool = False) -> Iterator[Sandbox]:
    tmp = Path(tempfile.mkdtemp(prefix="maestro_eval_"))
    root = tmp / "sandbox"
    home = tmp / "state"
    workspace = root / "workspace"
    for d in BASE_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)

    saved_env = {k: os.environ.get(k) for k in
                 ("MAESTRO_HOME", "MAESTRO_WORKSPACE", "MAESTRO_INTENT_MODEL")}
    os.environ["MAESTRO_HOME"] = str(home)
    os.environ["MAESTRO_WORKSPACE"] = str(workspace)

    saved_aliases = dict(_entities.FOLDER_ALIASES)
    _entities.FOLDER_ALIASES.clear()
    _entities.FOLDER_ALIASES.update(
        {alias: str(root / rel).replace("\\", "/") for alias, rel in
         SANDBOX_FOLDERS.items()}
    )
    # "home" must not silently resolve to the user's real home inside a sandbox.
    _entities.FOLDER_ALIASES["home"] = str(root).replace("\\", "/")
    _entities.FOLDER_ALIASES["home folder"] = str(root).replace("\\", "/")

    # The sandbox has to be allowed, and nothing else outside it. One wrinkle:
    # on Windows the system temp directory lives under ~/AppData, which is on
    # the default denylist — and the denylist beats the allowlist, by design. So
    # drop exactly those deny entries that are ANCESTORS of the sandbox root.
    # Every other deny rule survives, which is what the scope-escape cases in
    # the adversarial suite actually probe (~/.ssh, /etc, *.pem are all outside
    # the sandbox and stay denied).
    root_resolved = root.resolve()
    deny = [d for d in DEFAULT_DENY_DIRS
            if not _is_ancestor_of(PathPolicy._canon(d), root_resolved)]
    policy = PathPolicy(allow_roots=[str(root)], deny_dirs=deny)

    sb = Sandbox(root=root, home=home, workspace=workspace, policy=policy,
                 known_paths=dict(_entities.FOLDER_ALIASES))
    if fixture:
        build(sb, fixture)
    try:
        yield sb
    finally:
        _entities.FOLDER_ALIASES.clear()
        _entities.FOLDER_ALIASES.update(saved_aliases)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def build(sb: Sandbox, fixture: dict) -> None:
    """Materialise a declarative fixture.

    Declarative rather than a per-task Python script (which is what docs/07 §3
    sketches) because 100 setup scripts is 100 things that can drift. One
    interpreter, 100 data records — the fixture is then diffable and the same
    bytes are produced on macOS and on Windows, which the cross-platform
    equivalence test in docs/02 §7 depends on.
    """
    for d in fixture.get("dirs", []):
        sb.path(d).mkdir(parents=True, exist_ok=True)

    for spec in fixture.get("files", []):
        if isinstance(spec, str):
            spec = {"path": spec}
        p = sb.path(spec["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        if "content" in spec:
            p.write_text(spec["content"], encoding="utf-8")
        else:
            size = int(spec.get("size", 256))
            # Deterministic filler: the same fixture is byte-identical on every
            # platform and every re-run, so file counts and byte totals in the
            # dry-run preview are stable numbers the harness can assert on.
            p.write_bytes((f"MAESTRO fixture {spec['path']}\n".encode()
                           * (size // 32 + 1))[:size])
        if "mtime" in spec:
            import time

            when = time.time() - float(spec["mtime"]) * 86400
            os.utime(p, (when, when))

    for spec in fixture.get("many", []):
        # {"dir": "Downloads", "pattern": "invoice_{i:02d}.pdf", "n": 30}
        n = int(spec.get("n", 1))
        for i in range(1, n + 1):
            name = spec["pattern"].format(i=i)
            p = sb.path(f"{spec['dir']}/{name}")
            p.parent.mkdir(parents=True, exist_ok=True)
            size = int(spec.get("size", 256))
            p.write_bytes((f"MAESTRO fixture {name}\n".encode()
                           * (size // 32 + 1))[:size])
            if "mtime" in spec:
                import time

                when = time.time() - float(spec["mtime"]) * 86400
                os.utime(p, (when, when))
