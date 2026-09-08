"""Search executors — T2 in the PRD's task taxonomy.

    search.by_name     R0  find files whose name matches a query
    search.by_content  R0  find files containing a string  -> UNTRUSTED matches
    search.recent      R0  files modified within the last N days

Portable implementation on purpose. The architecture doc lists `mdfind` /
Windows Search as the per-platform backends; those are indexed and faster, but
they are also non-deterministic across machines (index state differs), which
would break the cross-platform differential test in docs/02 §7. A plain walk is
slower and *identical everywhere*, and identical everywhere is the property the
evaluation needs. The indexed backends live behind the same verb and can be
swapped in later without the planner noticing.
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel, Field

from maestro.executor.base import Context, EffectManifest, Result, register_executor, resolve
from maestro.ir import Risk
from maestro.registry import VerbSpec, register

MAX_SCAN = 20_000  # hard cap: a search is a read, but it is not a licence to walk /


class ByNameArgs(BaseModel):
    root: str
    query: str
    recursive: bool = True
    limit: int = Field(default=200, ge=1, le=5000)


class ByContentArgs(BaseModel):
    root: str
    query: str
    extensions: list[str] = Field(default_factory=lambda: [".txt", ".md", ".csv", ".log",
                                                           ".json", ".py"])
    limit: int = Field(default=100, ge=1, le=2000)
    max_bytes: int = Field(default=2_000_000, ge=1, le=50_000_000)


class RecentArgs(BaseModel):
    root: str
    days: int = Field(default=7, ge=1, le=3650)
    pattern: str = "*"
    limit: int = Field(default=200, ge=1, le=5000)


register(VerbSpec("search.by_name", ByNameArgs, Risk.R0, reversible=True, category="search",
                  description="Find files whose name contains a query string",
                  path_args=("root",)))
register(VerbSpec("search.by_content", ByContentArgs, Risk.R0, reversible=True,
                  category="search",
                  description="Find files containing a string (matches are UNTRUSTED)",
                  path_args=("root",)))
register(VerbSpec("search.recent", RecentArgs, Risk.R0, reversible=True, category="search",
                  description="Find files modified in the last N days", path_args=("root",)))


def _walk(root: Path, recursive: bool, pattern: str = "*"):
    if not root.is_dir():
        return
    it = root.rglob(pattern) if recursive else root.glob(pattern)
    for i, p in enumerate(it):
        if i >= MAX_SCAN:
            return
        if p.is_file():
            yield p


class ByNameExecutor:
    verb = "search.by_name"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = ByNameArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Search {a.root} for filenames containing {a.query!r}",
            unknowns=["result count is only known after the search runs"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = ByNameArgs.model_validate(resolve(args, ctx))
        q = a.query.lower()
        hits = [str(p) for p in _walk(Path(a.root).expanduser(), a.recursive)
                if q in p.name.lower()]
        hits = sorted(hits)[: a.limit]
        return Result(ok=True, output=hits, detail=f"{len(hits)} match(es)",
                      files_touched=len(hits))

    def undo(self, result: Result, ctx: Context) -> None:
        pass


class ByContentExecutor:
    verb = "search.by_content"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = ByContentArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Search the contents of files in {a.root} for {a.query!r}",
            unknowns=["matched text is UNTRUSTED content and never reaches the planner"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = ByContentArgs.model_validate(resolve(args, ctx))
        q = a.query.lower()
        exts = {e.lower() for e in a.extensions}
        hits: list[dict] = []
        for p in _walk(Path(a.root).expanduser(), True):
            if exts and p.suffix.lower() not in exts:
                continue
            try:
                if p.stat().st_size > a.max_bytes:
                    continue
                text = p.read_text(errors="replace")
            except OSError:
                continue
            idx = text.lower().find(q)
            if idx >= 0:
                hits.append({"path": str(p),
                             "excerpt": text[max(0, idx - 40): idx + 120]})
            if len(hits) >= a.limit:
                break
        # Excerpts are attacker-controllable text: T2.
        return Result(ok=True, output=hits, detail=f"{len(hits)} file(s) matched",
                      files_touched=len(hits), untrusted=True)

    def undo(self, result: Result, ctx: Context) -> None:
        pass


class RecentExecutor:
    verb = "search.recent"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = RecentArgs.model_validate(resolve(args, ctx))
        return EffectManifest(summary=f"Find files in {a.root} modified in the last "
                                      f"{a.days} day(s)")

    def execute(self, args: dict, ctx: Context) -> Result:
        a = RecentArgs.model_validate(resolve(args, ctx))
        cutoff = time.time() - a.days * 86400
        hits = []
        for p in _walk(Path(a.root).expanduser(), True, a.pattern):
            try:
                if p.stat().st_mtime >= cutoff:
                    hits.append(str(p))
            except OSError:
                continue
        hits = sorted(hits)[: a.limit]
        return Result(ok=True, output=hits, detail=f"{len(hits)} recent file(s)",
                      files_touched=len(hits))

    def undo(self, result: Result, ctx: Context) -> None:
        pass


for _ex in (ByNameExecutor(), ByContentExecutor(), RecentExecutor()):
    register_executor(_ex)
