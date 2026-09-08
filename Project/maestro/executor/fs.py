"""File-system executors — portable across macOS and Windows by construction
(pathlib / shutil / send2trash), so they are written exactly once.

Verbs
    fs.glob             R0  list files matching a pattern
    fs.list_dir         R0  immediate children of a directory
    fs.stat             R0  metadata for one path
    fs.read_text        R0  read a text file      -> UNTRUSTED output (T2)
    fs.mkdir            R1  create a directory
    fs.copy             R1  copy one file
    fs.copy_batch       R1  copy files into a directory
    fs.write_text       R2  create/overwrite a text file
    fs.rename           R2  rename one file in place
    fs.move_batch       R2  move files into a directory
    fs.trash            R2  Recycle Bin / Trash (never unlink)
    fs.restore_manifest R1  the declared inverse of fs.move_batch
    fs.delete_permanent R3  registered HARD-BLOCKED so refusal is testable

Two rules hold everywhere in this module:
  * no shell, ever. A filename is data (docs/02 §6 rule 5).
  * never overwrite silently — `_collision_safe` renames instead.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from maestro.executor.base import (
    Context,
    EffectManifest,
    NotAvailable,
    Result,
    register_executor,
    resolve,
)
from maestro.ir import Risk
from maestro.registry import VerbSpec, register

# --------------------------------------------------------------------------- #
# arg schemas
# --------------------------------------------------------------------------- #


class GlobArgs(BaseModel):
    root: str
    pattern: str = "*"
    recursive: bool = False


class ListDirArgs(BaseModel):
    path: str
    include_dirs: bool = True


class ReadTextArgs(BaseModel):
    path: str
    max_bytes: int = Field(default=1_000_000, ge=1, le=50_000_000)


class StatArgs(BaseModel):
    path: str


class MkdirArgs(BaseModel):
    path: str


class CopyArgs(BaseModel):
    src: str
    dst: str


class WriteTextArgs(BaseModel):
    path: str
    content: str = ""


class RenameArgs(BaseModel):
    path: str
    new_name: str


class MoveBatchArgs(BaseModel):
    sources: list[str]
    dest_dir: str


class CopyBatchArgs(BaseModel):
    sources: list[str]
    dest_dir: str


class TrashArgs(BaseModel):
    paths: list[str]


class RestoreManifestArgs(BaseModel):
    manifest: list[dict] = Field(default_factory=list)


class DeletePermanentArgs(BaseModel):
    paths: list[str]


# --------------------------------------------------------------------------- #
# verb registration (the closed registry)
# --------------------------------------------------------------------------- #

register(VerbSpec("fs.glob", GlobArgs, Risk.R0, reversible=True, category="file",
                  description="List files matching a pattern", path_args=("root",)))
register(VerbSpec("fs.list_dir", ListDirArgs, Risk.R0, reversible=True, category="file",
                  description="List the immediate children of a directory",
                  path_args=("path",)))
register(VerbSpec("fs.stat", StatArgs, Risk.R0, reversible=True, category="file",
                  description="File metadata (size, modified time, is_dir)",
                  path_args=("path",)))
register(VerbSpec("fs.read_text", ReadTextArgs, Risk.R0, reversible=True, category="file",
                  description="Read a text file (output is UNTRUSTED content)",
                  path_args=("path",)))
register(VerbSpec("fs.mkdir", MkdirArgs, Risk.R1, reversible=True, category="file",
                  description="Create a directory", path_args=("path",)))
register(VerbSpec("fs.copy", CopyArgs, Risk.R1, reversible=True, category="file",
                  description="Copy one file to a new path", path_args=("src", "dst")))
register(VerbSpec("fs.write_text", WriteTextArgs, Risk.R2, reversible=True, category="file",
                  description="Write a text file", path_args=("path",),
                  sensitive_args=("path",)))
register(VerbSpec("fs.rename", RenameArgs, Risk.R2, reversible=True, category="file",
                  description="Rename a file in place", path_args=("path",)))
register(VerbSpec("fs.copy_batch", CopyBatchArgs, Risk.R1, reversible=True, category="file",
                  description="Copy files into a destination directory",
                  path_args=("sources", "dest_dir"), sensitive_args=("dest_dir",)))
register(VerbSpec("fs.move_batch", MoveBatchArgs, Risk.R2, reversible=True, category="file",
                  description="Move files into a destination directory",
                  path_args=("sources", "dest_dir"), sensitive_args=("dest_dir",)))
register(VerbSpec("fs.trash", TrashArgs, Risk.R2, reversible=True, category="file",
                  description="Move files to the Recycle Bin / Trash",
                  path_args=("paths",), sensitive_args=("paths",)))
register(VerbSpec("fs.restore_manifest", RestoreManifestArgs, Risk.R1, reversible=True,
                  category="file", description="Undo a move using its manifest"))
# Exists so that refusing it is a tested behavior with a metric (HBR).
register(VerbSpec("fs.delete_permanent", DeletePermanentArgs, Risk.R3, reversible=False,
                  hard_blocked=True, category="file",
                  description="Permanent delete — always refused", path_args=("paths",)))


# --------------------------------------------------------------------------- #
# executors
# --------------------------------------------------------------------------- #


def _p(s: str) -> Path:
    return Path(str(s)).expanduser()


def _size(p: Path) -> int:
    try:
        return p.stat().st_size if p.is_file() else 0
    except OSError:
        return 0


def _sha256(p: Path) -> str | None:
    """Content fingerprint recorded in a move manifest at move time.

    `shutil.move` preserves bytes, so this is not there because the move might
    corrupt anything. It is there so that an undo can PROVE the restore rather
    than assert it: `fs.restore_manifest` re-hashes every file it puts back and
    reports any that differ. Reversibility is a claim the report makes; the hash
    is what makes it a measured one (URR, docs/07 §2).
    """
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


class GlobExecutor:
    verb = "fs.glob"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = GlobArgs.model_validate(resolve(args, ctx))
        matches = self._match(a)
        return EffectManifest(
            summary=f"Find files matching {a.pattern!r} in {a.root}",
            files_touched=len(matches),
            bytes_affected=sum(_size(m) for m in matches),
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = GlobArgs.model_validate(resolve(args, ctx))
        matches = [str(m) for m in self._match(a)]
        return Result(ok=True, output=matches, detail=f"{len(matches)} match(es)",
                      files_touched=len(matches))

    def undo(self, result: Result, ctx: Context) -> None:  # read-only
        pass

    @staticmethod
    def _match(a: GlobArgs) -> list[Path]:
        root = _p(a.root)
        if not root.is_dir():
            return []
        it = root.rglob(a.pattern) if a.recursive else root.glob(a.pattern)
        return sorted(x for x in it if x.is_file())


class ListDirExecutor:
    verb = "fs.list_dir"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = ListDirArgs.model_validate(resolve(args, ctx))
        entries = self._entries(a)
        return EffectManifest(summary=f"List {a.path}", files_touched=len(entries))

    def execute(self, args: dict, ctx: Context) -> Result:
        a = ListDirArgs.model_validate(resolve(args, ctx))
        p = _p(a.path)
        if not p.is_dir():
            return Result(ok=False, detail=f"{a.path} is not a directory")
        entries = [str(e) for e in self._entries(a)]
        return Result(ok=True, output=entries, detail=f"{len(entries)} entr(ies)",
                      files_touched=len(entries))

    def undo(self, result: Result, ctx: Context) -> None:
        pass

    @staticmethod
    def _entries(a: ListDirArgs) -> list[Path]:
        p = _p(a.path)
        if not p.is_dir():
            return []
        return sorted(e for e in p.iterdir() if a.include_dirs or e.is_file())


class ReadTextExecutor:
    verb = "fs.read_text"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = ReadTextArgs.model_validate(resolve(args, ctx))
        p = _p(a.path)
        return EffectManifest(
            summary=f"Read {a.path} (up to {a.max_bytes} bytes)",
            files_touched=1,
            bytes_affected=min(_size(p), a.max_bytes),
            unknowns=["file contents are UNTRUSTED and never reach the planner"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = ReadTextArgs.model_validate(resolve(args, ctx))
        p = _p(a.path)
        if not p.is_file():
            return Result(ok=False, detail=f"{a.path} is not a file")
        text = p.read_text(errors="replace")[: a.max_bytes]
        # This output is UNTRUSTED (T2). It may be shown to the user or given to
        # the tool-less Summarizer; it must never be fed to the Planner.
        return Result(ok=True, output=text, detail=f"read {len(text)} chars",
                      files_touched=1, untrusted=True)

    def undo(self, result: Result, ctx: Context) -> None:
        pass


class StatExecutor:
    verb = "fs.stat"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = StatArgs.model_validate(resolve(args, ctx))
        return EffectManifest(summary=f"Inspect metadata of {a.path}", files_touched=1)

    def execute(self, args: dict, ctx: Context) -> Result:
        a = StatArgs.model_validate(resolve(args, ctx))
        p = _p(a.path)
        if not p.exists():
            return Result(ok=False, detail=f"{a.path} does not exist")
        st = p.stat()
        return Result(ok=True, files_touched=1, output={
            "path": str(p), "name": p.name, "size": st.st_size,
            "is_dir": p.is_dir(), "modified": st.st_mtime,
        })

    def undo(self, result: Result, ctx: Context) -> None:
        pass


class MkdirExecutor:
    verb = "fs.mkdir"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = MkdirArgs.model_validate(resolve(args, ctx))
        exists = _p(a.path).is_dir()
        return EffectManifest(
            summary=f"Create directory {a.path}" + (" (already exists)" if exists else ""),
            creates=[] if exists else [a.path],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = MkdirArgs.model_validate(resolve(args, ctx))
        p = _p(a.path)
        created = not p.is_dir()
        p.mkdir(parents=True, exist_ok=True)
        return Result(ok=True, output=str(p), detail=("created" if created else "already existed"),
                      undo_data={"path": str(p), "created": created})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data
        if d and d.get("created"):
            p = Path(d["path"])
            if p.is_dir() and not any(p.iterdir()):  # only remove if still empty
                p.rmdir()


class CopyExecutor:
    verb = "fs.copy"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = CopyArgs.model_validate(resolve(args, ctx))
        src = _p(a.src)
        return EffectManifest(
            summary=f"Copy {a.src} -> {a.dst}",
            files_touched=1,
            bytes_affected=_size(src),
            creates=[a.dst],
            collisions=[a.dst] if _p(a.dst).exists() else [],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = CopyArgs.model_validate(resolve(args, ctx))
        src, dst = _p(a.src), _p(a.dst)
        if not src.is_file():
            return Result(ok=False, detail=f"{a.src} is not a file")
        dst.parent.mkdir(parents=True, exist_ok=True)
        final = _collision_safe(dst)
        shutil.copy2(src, final)
        return Result(ok=True, output=str(final), files_touched=1,
                      detail=f"copied to {final.name}", undo_data={"copy": str(final)})

    def undo(self, result: Result, ctx: Context) -> None:
        if result.undo_data:
            Path(result.undo_data["copy"]).unlink(missing_ok=True)


class WriteTextExecutor:
    verb = "fs.write_text"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = WriteTextArgs.model_validate(resolve(args, ctx))
        exists = _p(a.path).exists()
        return EffectManifest(
            summary=f"Write {len(a.content)} chars to {a.path}",
            files_touched=1,
            bytes_affected=len(a.content.encode()),
            creates=[] if exists else [a.path],
            modifies=[a.path] if exists else [],
            collisions=[f"{a.path} (existing file will be kept, new one renamed)"]
            if exists else [],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = WriteTextArgs.model_validate(resolve(args, ctx))
        p = _p(a.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        final = _collision_safe(p)
        final.write_text(a.content, encoding="utf-8")
        return Result(ok=True, output=str(final), files_touched=1,
                      detail=f"wrote {len(a.content)} chars",
                      undo_data={"written": str(final)})

    def undo(self, result: Result, ctx: Context) -> None:
        if result.undo_data:
            Path(result.undo_data["written"]).unlink(missing_ok=True)


class RenameExecutor:
    verb = "fs.rename"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = RenameArgs.model_validate(resolve(args, ctx))
        src = _p(a.path)
        target = src.with_name(a.new_name)
        return EffectManifest(
            summary=f"Rename {src.name} -> {a.new_name}",
            files_touched=1,
            modifies=[str(src)],
            collisions=[str(target)] if target.exists() else [],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = RenameArgs.model_validate(resolve(args, ctx))
        src = _p(a.path)
        if not src.exists():
            return Result(ok=False, detail=f"{a.path} does not exist")
        if "/" in a.new_name or "\\" in a.new_name:
            # A rename must not become a move — that would dodge the path policy
            # applied to `path` alone.
            return Result(ok=False, detail="new_name must be a bare filename")
        target = _collision_safe(src.with_name(a.new_name))
        src.rename(target)
        return Result(ok=True, output=str(target), files_touched=1,
                      undo_data={"from": str(src), "to": str(target)})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data or {}
        moved = Path(d.get("to", ""))
        if moved.exists():
            moved.rename(Path(d["from"]))


class CopyBatchExecutor:
    verb = "fs.copy_batch"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = CopyBatchArgs.model_validate(resolve(args, ctx))
        dest = _p(a.dest_dir)
        collisions = [s for s in a.sources if (dest / Path(s).name).exists()]
        return EffectManifest(
            summary=f"Copy {len(a.sources)} file(s) -> {a.dest_dir}",
            files_touched=len(a.sources),
            bytes_affected=sum(_size(_p(s)) for s in a.sources),
            creates=[str(dest / Path(s).name) for s in a.sources],
            collisions=[f"{Path(c).name} (will be renamed, never overwritten)"
                        for c in collisions],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = CopyBatchArgs.model_validate(resolve(args, ctx))
        dest = _p(a.dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        made: list[str] = []
        for s in a.sources:
            src = _p(s)
            if not src.is_file():
                return Result(ok=False, detail=f"{s} is not a file", output=made,
                              undo_data={"copies": made}, files_touched=len(made))
            target = _collision_safe(dest / src.name)
            shutil.copy2(src, target)
            made.append(str(target))
        return Result(ok=True, output=made, detail=f"copied {len(made)} file(s)",
                      undo_data={"copies": made}, files_touched=len(made))

    def undo(self, result: Result, ctx: Context) -> None:
        for c in (result.undo_data or {}).get("copies", []):
            Path(c).unlink(missing_ok=True)


class MoveBatchExecutor:
    verb = "fs.move_batch"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = MoveBatchArgs.model_validate(resolve(args, ctx))
        dest = _p(a.dest_dir)
        collisions = [s for s in a.sources if (dest / Path(s).name).exists()]
        size = sum(_size(_p(s)) for s in a.sources)
        return EffectManifest(
            summary=f"Move {len(a.sources)} file(s) -> {a.dest_dir}",
            files_touched=len(a.sources),
            bytes_affected=size,
            modifies=list(a.sources),
            collisions=[f"{Path(c).name} (will be renamed, never overwritten)"
                        for c in collisions],
            creates=[] if dest.is_dir() else [a.dest_dir],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = MoveBatchArgs.model_validate(resolve(args, ctx))
        dest = _p(a.dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        manifest: list[dict] = []  # [{"from": ..., "to": ...}] — this IS the undo
        for s in a.sources:
            src = _p(s)
            if not src.is_file():
                # Halt on first miss; already-moved files stay recorded so the
                # orchestrator can roll back cleanly.
                return Result(ok=False, detail=f"{s} is not a file",
                              output=manifest, undo_data=manifest,
                              files_touched=len(manifest))
            target = _collision_safe(dest / src.name)
            digest = _sha256(src)
            shutil.move(str(src), str(target))
            manifest.append({"from": str(src), "to": str(target), "sha256": digest})
        # The output IS the manifest, not just the destination paths. Two things
        # depend on it being the full [{from, to}] form: the declared undo
        # (`fs.restore_manifest(manifest=$moved)`) needs the origins to put the
        # files back, and the `all_moved` postcondition needs both sides to tell
        # a real move from a half-move. Returning bare destinations made both of
        # those silently no-ops — the undo restored nothing and the
        # postcondition passed vacuously.
        return Result(ok=True, output=manifest,
                      detail=f"moved {len(manifest)} file(s)", undo_data=manifest,
                      files_touched=len(manifest))

    def undo(self, result: Result, ctx: Context) -> None:
        for entry in reversed(result.undo_data or []):
            src, moved_to = Path(entry["from"]), Path(entry["to"])
            if moved_to.is_file():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved_to), str(src))


class RestoreManifestExecutor:
    """The verb `fs.move_batch` names in its `undo` spec, so a plan can declare
    its own inverse rather than relying on runtime state."""

    verb = "fs.restore_manifest"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = RestoreManifestArgs.model_validate(resolve(args, ctx))
        return EffectManifest(summary=f"Restore {len(a.manifest)} file(s) to their "
                                      f"original locations", files_touched=len(a.manifest))

    def execute(self, args: dict, ctx: Context) -> Result:
        a = RestoreManifestArgs.model_validate(resolve(args, ctx))
        restored: list[str] = []
        missing: list[str] = []      # the moved file is no longer where we put it
        collided: list[str] = []     # something else now occupies the origin
        mismatched: list[str] = []   # restored, but the bytes differ from move time
        for entry in reversed(a.manifest):
            moved_to = Path(str(entry.get("to", "")))
            origin = Path(str(entry.get("from", "")))
            if not str(origin):
                continue
            if not moved_to.is_file():
                missing.append(str(moved_to))
                continue
            if origin.exists():
                # Never overwrite. The user (or another plan) put something at
                # the origin since the move; restoring on top of it would
                # destroy that. Put the file beside it and say so.
                collided.append(str(origin))
                origin = _collision_safe(origin)
            origin.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(moved_to), str(origin))
            restored.append(str(origin))
            expected = entry.get("sha256")
            if expected and _sha256(origin) != expected:
                mismatched.append(str(origin))

        ok = not missing and not mismatched
        bits = [f"restored {len(restored)} file(s)"]
        if collided:
            bits.append(f"{len(collided)} origin(s) were occupied; restored beside them")
        if missing:
            bits.append(f"{len(missing)} file(s) no longer at their moved location")
        if mismatched:
            bits.append(f"{len(mismatched)} file(s) differ from their move-time hash")
        return Result(ok=ok, output={"restored": restored, "collided": collided,
                                     "missing": missing, "mismatched": mismatched},
                      detail="; ".join(bits), files_touched=len(restored))

    def undo(self, result: Result, ctx: Context) -> None:
        pass


class TrashExecutor:
    verb = "fs.trash"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = TrashArgs.model_validate(resolve(args, ctx))
        existing = [p for p in a.paths if _p(p).exists()]
        return EffectManifest(
            summary=f"Move {len(existing)} item(s) to the Recycle Bin / Trash",
            files_touched=len(existing),
            bytes_affected=sum(_size(_p(p)) for p in existing),
            removes=list(existing),
            unknowns=["restore-from-Trash is a manual step on some platforms"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        try:
            from send2trash import send2trash
        except ImportError as e:  # pragma: no cover - environment dependent
            raise NotAvailable("send2trash is not installed; refusing to unlink") from e

        a = TrashArgs.model_validate(resolve(args, ctx))
        trashed = []
        for p in a.paths:
            path = _p(p)
            if path.exists():
                send2trash(str(path))
                trashed.append(str(path))
        return Result(ok=True, output=trashed, detail=f"trashed {len(trashed)} item(s)",
                      files_touched=len(trashed), undo_data={"trashed": trashed})

    def undo(self, result: Result, ctx: Context) -> None:
        # Programmatic restore from Trash is platform-specific; v1 treats trash
        # as manually reversible (it IS the safety mechanism vs unlink).
        raise NotImplementedError("restore from Trash is a manual step")


def _collision_safe(target: Path) -> Path:
    """foo.pdf -> foo (1).pdf -> foo (2).pdf ... never overwrite."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        cand = target.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(f"could not find a collision-free name for {target}")


for _ex in (
    GlobExecutor(), ListDirExecutor(), ReadTextExecutor(), StatExecutor(),
    MkdirExecutor(), CopyExecutor(), CopyBatchExecutor(), WriteTextExecutor(),
    RenameExecutor(), MoveBatchExecutor(), RestoreManifestExecutor(), TrashExecutor(),
):
    register_executor(_ex)
