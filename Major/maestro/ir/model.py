"""The Action IR — the single typed contract between planner and executors.

Everything that crosses the planner->safety->executor boundary is one of these
models. Free-form text never crosses it. See docs/02-ARCHITECTURE.md §3.
"""

from __future__ import annotations

import re
from enum import IntEnum
from graphlib import CycleError, TopologicalSorter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A `$var` reference to the output of an earlier action, e.g. "$pdf_list".
VAR_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")


class Risk(IntEnum):
    """Ordered risk tiers. Comparisons are meaningful: R2 > R1.

    BLOCKED is deliberately above R3: once a rule says "blocked", no other
    rule can lower it (monotonic escalation, docs/06-SAFETY-SPEC.md §2).
    """

    R0 = 0  # pure read, no state change
    R1 = 1  # reversible, inside declared workspace
    R2 = 2  # reversible but consequential / outside workspace -> consent
    R3 = 3  # irreversible / security-relevant -> typed consent
    BLOCKED = 4  # hard-blocked, no override path

    def __str__(self) -> str:  # pretty for previews and logs
        return self.name


class Check(BaseModel):
    """A pre- or postcondition. Interpreted by the orchestrator, not the LLM."""

    model_config = ConfigDict(extra="forbid")

    check: str  # e.g. "path_exists", "dir_writable", "var_defined", "all_moved"
    args: dict[str, Any] = Field(default_factory=dict)


class UndoSpec(BaseModel):
    """Declared inverse of an action, bound at plan time (docs/02 §3.2)."""

    model_config = ConfigDict(extra="forbid")

    verb: str
    args: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(pattern=r"^a\d+$")
    verb: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    produces: str | None = None
    # Planner's risk *hint*. Recorded, compared, never trusted — the
    # deterministic scorer overwrites it (docs/06 §2).
    risk_hint: Risk | None = None
    undo: UndoSpec | None = None
    preconditions: list[Check] = Field(default_factory=list)
    postconditions: list[Check] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("produces")
    @classmethod
    def _produces_is_identifier(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", v):
            raise ValueError(f"produces must be a bare identifier, got {v!r}")
        return v

    def var_refs(self) -> set[str]:
        """All `$var` names referenced anywhere in args (recursively)."""

        refs: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, str):
                m = VAR_RE.match(value)
                if m:
                    refs.add(m.group(1))
            elif isinstance(value, dict):
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)

        walk(self.args)
        return refs


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=20, ge=1, le=100)
    max_seconds: int = Field(default=120, ge=1, le=3600)
    max_files_touched: int = Field(default=500, ge=1, le=100_000)


class Plan(BaseModel):
    """A validated DAG of actions. Construction *is* structural validation:

    - unique action ids
    - dependencies reference existing actions
    - no cycles
    - every `$var` reference is produced by a (transitive) dependency
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    instruction: str
    actions: list[Action] = Field(min_length=1)
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def _validate_dag(self) -> "Plan":
        ids = [a.action_id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate action_id in plan")
        known = set(ids)
        for a in self.actions:
            for dep in a.depends_on:
                if dep not in known:
                    raise ValueError(f"{a.action_id} depends on unknown action {dep!r}")

        # Cycle check + topological order (stdlib does the work).
        ts = TopologicalSorter({a.action_id: set(a.depends_on) for a in self.actions})
        try:
            order = list(ts.static_order())
        except CycleError as e:
            raise ValueError(f"dependency cycle in plan: {e.args[1]}") from e
        self._topo_order = order

        # Every $var must be produced by an action this one depends on
        # (transitively). Anything else is unresolvable dataflow.
        producers: dict[str, str] = {}
        for a in self.actions:
            if a.produces:
                if a.produces in producers:
                    raise ValueError(f"variable {a.produces!r} produced twice")
                producers[a.produces] = a.action_id

        ancestors: dict[str, set[str]] = {}
        by_id = {a.action_id: a for a in self.actions}
        for aid in order:
            acc: set[str] = set()
            for dep in by_id[aid].depends_on:
                acc |= {dep} | ancestors[dep]
            ancestors[aid] = acc

        for a in self.actions:
            for ref in a.var_refs():
                producer = producers.get(ref)
                if producer is None:
                    raise ValueError(f"{a.action_id} references undefined ${ref}")
                if producer not in ancestors[a.action_id]:
                    raise ValueError(
                        f"{a.action_id} references ${ref} but does not depend on "
                        f"its producer {producer}"
                    )
        return self

    @property
    def topo_order(self) -> list[str]:
        return list(self._topo_order)

    def action(self, action_id: str) -> Action:
        for a in self.actions:
            if a.action_id == action_id:
                return a
        raise KeyError(action_id)
