"""File-system executors — portable across macOS and Windows by construction
(pathlib / shutil / send2trash), so they are written exactly once.

Verbs: fs.glob, fs.read_text, fs.stat, fs.mkdir, fs.copy, fs.move_batch, fs.trash
Plus fs.delete_permanent — registered hard-blocked so refusal is testable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from maestro.executor.base import Context, EffectManifest, Result, register_executor, resolve
from maestro.ir import Risk
from maestro.registry import VerbSpec, register

# --------------------------------------------------------------------------- #
# arg schemas
# --------------------------------------------------------------------------- #


class GlobArgs(BaseModel):
    root: str
    pattern: str = "*"
    recursive: bool = False


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


class MoveBatchArgs(BaseModel):
    sources: list[str]
    dest_dir: str


class TrashArgs(BaseModel):
    paths: list[str]


class DeletePermanentArgs(BaseModel):
    paths: list[str]


# --------------------------------------------------------------------------- #
# verb registration (the closed registry)
# --------------------------------------------------------------------------- #

register(VerbSpec("fs.glob", GlobArgs, Risk.R0, reversible=True,
                  description="List files matching a pattern", path_args=("root",)))
register(VerbSpec("fs.read_text", ReadTextArgs, Risk.R0, reversible=True,
                  description="Read a text file", path_args=("path",)))
register(VerbSpec("fs.stat", StatArgs, Risk.R0, reversible=True,
                  description="File metadata", path_args=("path",)))
register(VerbSpec("fs.mkdir", MkdirArgs, Risk.R1, reversible=True,
                  description="Create a directory", path_args=("path",)))
register(VerbSpec("fs.copy", CopyArgs, Risk.R1, reversible=True,
                  description="Copy a file", path_args=("src", "dst")))
register(VerbSpec("fs.move_batch", MoveBatchArgs, Risk.R2, reversible=True,
                  description="Move files into a directory", path_args=("sources", "dest_dir")))
register(VerbSpec("fs.trash", TrashArgs, Risk.R2, reversible=True,
                  description="Move files to Trash/Recycle Bin", path_args=("paths",)))
# Exists so that refusing it is a tested behavior with a metric (HBR).
register(VerbSpec("fs.delete_permanent", DeletePermanentArgs, Risk.R3, reversible=False,
                  hard_blocked=True, description="Permanent delete — always refused",
                  path_args=("paths",)))


# --------------------------------------------------------------------------- #
# executors
# --------------------------------------------------------------------------- #


def _p(s: str) -> Path:
    return Path(s).expanduser()


class GlobExecutor:
    verb = "fs.glob"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = GlobArgs.model_validate(resolve(args, ctx))
        matches = self._match(a)
        return EffectManifest(
            summary=f"Find files matching {a.pattern!r} in {a.root}",
            files_touched=len(matches),
            bytes_affected=sum(m.stat().st_size for m in matches if m.is_file()),
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = GlobArgs.model_validate(resolve(args, ctx))
        matches = [str(m) for m in self._match(a)]
        return Result(ok=True, output=matches, detail=f"{len(matches)} match(es)")

    def undo(self, result: Result, ctx: Context) -> None:  # read-only
        pass

    @staticmethod
    def _match(a: GlobArgs) -> list[Path]:
        root = _p(a.root)
        if not root.is_dir():
            return []
        it = root.rglob(a.pattern) if a.recursive else root.glob(a.pattern)
        return sorted(x for x in it if x.is_file())


class ReadTextExecutor:
    verb = "fs.read_text"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = ReadTextArgs.model_validate(resolve(args, ctx))
        p = _p(a.path)
        size = p.stat().st_size if p.is_file() else 0
        return EffectManifest(
            summary=f"Read {a.path} (up to {a.max_bytes} bytes)",
            files_touched=1,
            bytes_affected=min(size, a.max_bytes),
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = ReadTextArgs.model_validate(resolve(args, ctx))
        p = _p(a.path)
        if not p.is_file():
            return Result(ok=False, detail=f"{a.path} is not a file")
        text = p.read_text(errors="replace")[: a.max_bytes]
        # NOTE: this output is UNTRUSTED (T2). It may be shown to the user or
        # the tool-less Summarizer; it must never be fed to the Planner.
        return Result(ok=True, output=text, detail=f"read {len(text)} chars")

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
        return Result(ok=True, output={
            "path": str(p), "size": st.st_size, "is_dir": p.is_dir(),
            "modified": st.st_mtime,
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
        return Result(ok=True, output=str(p), undo_data={"path": str(p), "created": created})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data
        if d and d["created"]:
            p = Path(d["path"])
            if p.is_dir() and not any(p.iterdir()):  # only remove if still empty
                p.rmdir()


class CopyExecutor:
    verb = "fs.copy"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = CopyArgs.model_validate(resolve(args, ctx))
        src = _p(a.src)
        size = src.stat().st_size if src.is_file() else 0
        collision = _p(a.dst).exists()
        return EffectManifest(
            summary=f"Copy {a.src} -> {a.dst}",
            files_touched=1,
            bytes_affected=size,
            creates=[a.dst],
            collisions=[a.dst] if collision else [],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = CopyArgs.model_validate(resolve(args, ctx))
        src, dst = _p(a.src), _p(a.dst)
        if not src.is_file():
            return Result(ok=False, detail=f"{a.src} is not a file")
        dst.parent.mkdir(parents=True, exist_ok=True)
        final = _collision_safe(dst)
        shutil.copy2(src, final)
        return Result(ok=True, output=str(final), undo_data={"copy": str(final)})

    def undo(self, result: Result, ctx: Context) -> None:
        if result.undo_data:
            Path(result.undo_data["copy"]).unlink(missing_ok=True)


class MoveBatchExecutor:
    verb = "fs.move_batch"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = MoveBatchArgs.model_validate(resolve(args, ctx))
        dest = _p(a.dest_dir)
        collisions = [s for s in a.sources if (dest / Path(s).name).exists()]
        size = sum(_p(s).stat().st_size for s in a.sources if _p(s).is_file())
        return EffectManifest(
            summary=f"Move {len(a.sources)} file(s) -> {a.dest_dir}",
            files_touched=len(a.sources),
            bytes_affected=size,
            modifies=list(a.sources),
            collisions=[f"{c} (will be renamed)" for c in collisions],
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
                # Halt on first miss; already-moved files stay recorded so
                # the orchestrator can roll back cleanly.
                return Result(ok=False, detail=f"{s} is not a file",
                              output=[m["to"] for m in manifest], undo_data=manifest)
            target = _collision_safe(dest / src.name)
            shutil.move(str(src), str(target))
            manifest.append({"from": str(src), "to": str(target)})
        return Result(ok=True, output=[m["to"] for m in manifest],
                      detail=f"moved {len(manifest)} file(s)", undo_data=manifest)

    def undo(self, result: Result, ctx: Context) -> None:
        for entry in reversed(result.undo_data or []):
            src, moved_to = Path(entry["from"]), Path(entry["to"])
            if moved_to.is_file():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved_to), str(src))


class TrashExecutor:
    verb = "fs.trash"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = TrashArgs.model_validate(resolve(args, ctx))
        existing = [p for p in a.paths if _p(p).exists()]
        size = sum(_p(p).stat().st_size for p in existing if _p(p).is_file())
        return EffectManifest(
            summary=f"Move {len(existing)} item(s) to Trash",
            files_touched=len(existing),
            bytes_affected=size,
            removes=list(existing),
            unknowns=(["restore-from-Trash is manual on some platforms"]),
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        from send2trash import send2trash

        a = TrashArgs.model_validate(resolve(args, ctx))
        trashed = []
        for p in a.paths:
            path = _p(p)
            if path.exists():
                send2trash(str(path))
                trashed.append(str(path))
        return Result(ok=True, output=trashed, detail=f"trashed {len(trashed)} item(s)",
                      undo_data={"trashed": trashed})

    def undo(self, result: Result, ctx: Context) -> None:
        # Programmatic restore from Trash is platform-specific; v0 treats
        # trash as manually reversible (it IS the safety mechanism vs unlink).
        raise NotImplementedError("restore from Trash is manual in v0")


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


for _ex in (GlobExecutor(), ReadTextExecutor(), StatExecutor(), MkdirExecutor(),
            CopyExecutor(), MoveBatchExecutor(), TrashExecutor()):
    register_executor(_ex)
