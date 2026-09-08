"""Executor interface (docs/02-ARCHITECTURE.md §7).

Every verb has exactly one Executor per platform. File / search / browser verbs
are portable and written once; app / system / draft verbs dispatch to
`darwin/` or `win32/`. Nothing above this layer may branch on the platform —
`scripts/check_platform_boundary.py` greps for it in CI.

The four-method contract is what makes the whole safety story work:

    validate()  typed args, before anything
    dry_run()   what WOULD happen — mandatory, feeds the consent preview
    execute()   do it, and return enough state to undo it
    undo()      the declared inverse
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from maestro.ir.model import VAR_RE


@dataclass
class Context:
    """Execution context threaded through every executor call.

    `session` holds resources that must outlive a single verb but not the plan
    — today the browser session (`maestro.executor.browser.BrowserSession`).
    The orchestrator owns the lifecycle: it creates one Context per plan and
    calls `close()` on every exit path, so a browser opened by step 1 is still
    there for step 4 and is gone when the plan is, whether the plan succeeded,
    failed, or was rolled back.
    """

    variables: dict[str, Any] = field(default_factory=dict)
    workspace: str | None = None
    dry: bool = False
    session: Any = None

    def bind(self, name: str, value: Any) -> None:
        self.variables[name] = value

    def get(self, name: str, default: Any = None) -> Any:
        return self.variables.get(name, default)

    def close(self) -> None:
        """Release plan-scoped resources. Idempotent; never raises."""
        sess, self.session = self.session, None
        if sess is not None and hasattr(sess, "close"):
            try:
                sess.close()
            except Exception:
                pass


class UnboundVariable(KeyError):
    """A `$var` was referenced before its producer ran. Fail closed, never guess."""


def resolve(args: dict, ctx: Context) -> dict:
    """Substitute `$var` references with their bound values."""

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            m = VAR_RE.match(v)
            if m:
                name = m.group(1)
                if name not in ctx.variables:
                    raise UnboundVariable(f"unbound variable ${name}")
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
    """What an action WOULD do — produced by dry_run, shown to the user before
    consent. Honest by construction: anything unpredictable goes in `unknowns`
    rather than being silently omitted (docs/06 §3)."""

    summary: str
    files_touched: int = 0
    bytes_affected: int = 0
    creates: list[str] = field(default_factory=list)
    modifies: list[str] = field(default_factory=list)
    removes: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)  # network destinations

    @property
    def predictable(self) -> bool:
        return not self.unknowns


@dataclass
class Result:
    ok: bool
    output: Any = None  # bound to the action's `produces` variable
    detail: str = ""
    undo_data: Any = None  # whatever undo() needs (e.g. a move manifest)
    files_touched: int = 0
    # Set True by executors that return attacker-controllable content. The
    # orchestrator refuses to let this reach anything but the Summarizer.
    untrusted: bool = False


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


def has_executor(verb: str) -> bool:
    return verb in _EXECUTORS


def registered_verbs() -> list[str]:
    return sorted(_EXECUTORS)


class NotAvailable(RuntimeError):
    """An optional backend (Playwright, pywinauto, ...) is not installed.

    Raised as a *step failure*, never as a crash: a missing browser must degrade
    the task, not the process.
    """
