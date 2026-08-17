"""Executor interface (docs/02-ARCHITECTURE.md §7).

Every verb has exactly one Executor per platform. File/search/browser verbs
are portable and written once; only app/system verbs will need
darwin/ and win32/ subpackages later. Nothing above this layer may branch
on the platform — CI greps for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from maestro.ir.model import VAR_RE


@dataclass
class Context:
    """Execution context threaded through every executor call."""

    variables: dict[str, Any] = field(default_factory=dict)

    def bind(self, name: str, value: Any) -> None:
        self.variables[name] = value


def resolve(args: dict, ctx: Context) -> dict:
    """Substitute `$var` references with their bound values. Fail closed on
    unbound references — an unresolvable variable is a plan bug, not a guess."""

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            m = VAR_RE.match(v)
            if m:
                name = m.group(1)
                if name not in ctx.variables:
                    raise KeyError(f"unbound variable ${name}")
                return ctx.variables[name]
            return v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return {k: walk(v) for k, v in args.items()}


@dataclass
class EffectManifest:
    """What an action WOULD do — produced by dry_run, shown to the user
    before consent. Honest by construction: anything unpredictable goes in
    `unknowns` rather than being silently omitted (docs/06 §3)."""

    summary: str
    files_touched: int = 0
    bytes_affected: int = 0
    creates: list[str] = field(default_factory=list)
    modifies: list[str] = field(default_factory=list)
    removes: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


@dataclass
class Result:
    ok: bool
    output: Any = None  # bound to the action's `produces` variable
    detail: str = ""
    undo_data: Any = None  # whatever undo() needs (e.g. a move manifest)


@runtime_checkable
class Executor(Protocol):
    verb: str

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest: ...

    def execute(self, args: dict, ctx: Context) -> Result: ...

    def undo(self, result: Result, ctx: Context) -> None: ...


_EXECUTORS: dict[str, Executor] = {}


def register_executor(ex: Executor) -> Executor:
    if ex.verb in _EXECUTORS:
        raise ValueError(f"executor for {ex.verb!r} registered twice")
    _EXECUTORS[ex.verb] = ex
    return ex


def get_executor(verb: str) -> Executor:
    try:
        return _EXECUTORS[verb]
    except KeyError:
        raise KeyError(f"no executor registered for verb {verb!r}") from None
