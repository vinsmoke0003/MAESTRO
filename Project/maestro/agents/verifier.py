"""The Verifier — postcondition checking (FR-42, threat T8).

> "Without postconditions the agent reports success whenever a call didn't
>  raise, which is the single most common way agent benchmarks lie."
>  — docs/02-ARCHITECTURE.md §3.2

So success is not "the executor returned ok=True". Success is "the declared
postcondition holds against the real filesystem afterwards". Ablation A3 in
docs/07 §5 removes this module, and the expected result is a large jump in
silent failures.

There is no LLM here either. `CHECKS` is a closed set of named predicates, in
exactly the same spirit as the closed verb registry: a plan cannot invent a
postcondition that always passes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maestro.executor.base import Context
from maestro.ir import Check


@dataclass(frozen=True)
class CheckResult:
    check: str
    ok: bool
    detail: str = ""


def _p(v: Any) -> Path:
    return Path(str(v)).expanduser()


# --------------------------------------------------------------------------- #
# the closed set of checks
# --------------------------------------------------------------------------- #


def _path_exists(args: dict, ctx: Context) -> CheckResult:
    p = _p(_bind(args.get("path"), ctx))
    return CheckResult("path_exists", p.exists(), f"{p} {'exists' if p.exists() else 'missing'}")


def _path_absent(args: dict, ctx: Context) -> CheckResult:
    p = _p(_bind(args.get("path"), ctx))
    return CheckResult("path_absent", not p.exists(), f"{p} {'still present' if p.exists() else 'absent'}")


def _dir_exists(args: dict, ctx: Context) -> CheckResult:
    p = _p(_bind(args.get("path"), ctx))
    return CheckResult("dir_exists", p.is_dir(), f"{p} is{'' if p.is_dir() else ' not'} a directory")


def _dir_writable(args: dict, ctx: Context) -> CheckResult:
    import os

    p = _p(_bind(args.get("path"), ctx))
    target = p if p.is_dir() else p.parent
    ok = target.is_dir() and os.access(target, os.W_OK)
    return CheckResult("dir_writable", ok, f"{target} writable={ok}")


def _var_defined(args: dict, ctx: Context) -> CheckResult:
    name = str(args.get("var", "")).lstrip("$")
    ok = name in ctx.variables
    return CheckResult("var_defined", ok, f"${name} {'bound' if ok else 'unbound'}")


def _var_nonempty(args: dict, ctx: Context) -> CheckResult:
    name = str(args.get("var", "")).lstrip("$")
    value = ctx.variables.get(name)
    ok = bool(value)
    n = len(value) if isinstance(value, (list, str, dict)) else ("1" if value else 0)
    return CheckResult("var_nonempty", ok, f"${name} has {n} item(s)")


def _count_eq(args: dict, ctx: Context) -> CheckResult:
    name = str(args.get("var", "")).lstrip("$")
    expected = int(args.get("count", 0))
    value = ctx.variables.get(name) or []
    actual = len(value) if hasattr(value, "__len__") else 0
    return CheckResult("count_eq", actual == expected, f"${name}: {actual} vs expected {expected}")


def _all_moved(args: dict, ctx: Context) -> CheckResult:
    """The move manifest is truthful: every `to` exists and every `from` is gone.

    This is the check that catches the classic lie — `shutil.move` succeeding on
    the first file, failing on the fourth, and the step still reporting success.
    """
    manifest = _bind(args.get("manifest"), ctx) or []
    if isinstance(manifest, dict):
        manifest = [manifest]
    missing, leftover = [], []
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        to, frm = entry.get("to"), entry.get("from")
        if to and not _p(to).exists():
            missing.append(str(to))
        if frm and _p(frm).exists():
            leftover.append(str(frm))
    ok = not missing and not leftover
    detail = "all files moved" if ok else (
        f"{len(missing)} missing at destination, {len(leftover)} still at source"
    )
    return CheckResult("all_moved", ok, detail)


def _files_in_dir(args: dict, ctx: Context) -> CheckResult:
    d = _p(_bind(args.get("path"), ctx))
    pattern = str(args.get("pattern", "*"))
    at_least = int(args.get("at_least", 1))
    n = len(list(d.glob(pattern))) if d.is_dir() else 0
    return CheckResult("files_in_dir", n >= at_least,
                       f"{d} contains {n} match(es) for {pattern!r}, need >= {at_least}")


def _file_contains(args: dict, ctx: Context) -> CheckResult:
    p = _p(_bind(args.get("path"), ctx))
    needle = str(args.get("text", ""))
    if not p.is_file():
        return CheckResult("file_contains", False, f"{p} is not a file")
    ok = needle in p.read_text(errors="replace")
    return CheckResult("file_contains", ok, f"{p} {'contains' if ok else 'lacks'} {needle!r}")


CHECKS: dict[str, Callable[[dict, Context], CheckResult]] = {
    "path_exists": _path_exists,
    "path_absent": _path_absent,
    "dir_exists": _dir_exists,
    "dir_writable": _dir_writable,
    "var_defined": _var_defined,
    "var_nonempty": _var_nonempty,
    "count_eq": _count_eq,
    "all_moved": _all_moved,
    "files_in_dir": _files_in_dir,
    "file_contains": _file_contains,
}


class Verifier:
    """Evaluates a list of Checks against the live context."""

    def run(self, checks: list[Check], ctx: Context) -> list[CheckResult]:
        out: list[CheckResult] = []
        for c in checks:
            fn = CHECKS.get(c.check)
            if fn is None:
                # An unknown check must FAIL, not be skipped. Skipping would let
                # a planner disable verification by inventing a check name.
                out.append(CheckResult(c.check, False, f"unknown check {c.check!r}"))
                continue
            try:
                out.append(fn(c.args, ctx))
            except Exception as e:  # a check must never crash the run
                out.append(CheckResult(c.check, False, f"{type(e).__name__}: {e}"))
        return out

    @staticmethod
    def all_ok(results: list[CheckResult]) -> bool:
        return all(r.ok for r in results)

    @staticmethod
    def summarize(results: list[CheckResult]) -> str:
        failed = [r for r in results if not r.ok]
        if not failed:
            return f"{len(results)} postcondition(s) satisfied"
        return "; ".join(f"{r.check}: {r.detail}" for r in failed)


def _bind(value: Any, ctx: Context) -> Any:
    """Resolve `$var` in a check argument, leaving literals alone."""
    from maestro.ir.model import VAR_RE

    if isinstance(value, str):
        m = VAR_RE.match(value)
        if m:
            return ctx.variables.get(m.group(1))
    return value
