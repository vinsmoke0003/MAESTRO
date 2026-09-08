"""The Action IR — the single typed contract between planner and executors.

Everything that crosses the planner -> safety -> executor boundary is one of
these models. Free-form text never crosses it (docs/02-ARCHITECTURE.md §3).

v1.0 additions over the minor-project v0.2 IR:
  * `instruction_hash` binds a plan to the exact bytes of the user's input
  * `created_at` / `planner` provenance block
  * `trust` on every action — how trusted the *inputs* of this action are (T0/T1/T2)
  * canonicalisation (`canonical()`) so two plans can be compared for the
    Plan-Exact-Match metric without being byte-identical
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import IntEnum
from graphlib import CycleError, TopologicalSorter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A `$var` reference to the output of an earlier action, e.g. "$pdf_list".
VAR_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


class Trust(IntEnum):
    """Trust level of the data flowing into an action (docs/02 §6).

    T0 TRUSTED   the user's own instruction, config, the verb registry
    T1 DERIVED   produced by MAESTRO from T0 (plans, globbed paths, entities)
    T2 UNTRUSTED file contents, web pages, tool stdout, filenames, OCR text

    Trust is monotone downward: an action whose inputs include T2 data is
    itself T2, and the scorer escalates its risk for that reason alone.
    """

    T0 = 0
    T1 = 1
    T2 = 2

    def __str__(self) -> str:
        return self.name


class Check(BaseModel):
    """A pre- or postcondition. Interpreted by the Verifier, never by the LLM."""

    model_config = ConfigDict(extra="forbid")

    check: str  # see maestro/agents/verifier.py CHECKS for the closed set
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
    # Set by the taint tracker, not by the planner. Present in the model so it
    # survives serialisation into the audit log and the dataset.
    trust: Trust = Trust.T1

    @field_validator("produces")
    @classmethod
    def _produces_is_identifier(cls, v: str | None) -> str | None:
        if v is not None and not IDENT_RE.match(v):
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

    def canonical(self) -> dict[str, Any]:
        """Comparison form for Plan-Exact-Match / Action-F1 (docs/07 §2).

        Drops everything that is provenance rather than semantics: rationale
        text, risk hints, trust labels, and the ordering of dict keys. Paths
        are normalised so `~/Downloads`, `~/Downloads/`, and `$HOME/Downloads`
        compare equal.
        """
        return {
            "verb": self.verb,
            "args": _canon_value(self.args),
            "produces": self.produces,
            "depends_on": sorted(self.depends_on),
        }


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=20, ge=1, le=100)
    max_seconds: int = Field(default=120, ge=1, le=3600)
    max_files_touched: int = Field(default=500, ge=1, le=100_000)


class PlannerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = ""
    version: str = ""
    strategy: str = ""  # llm | rule | template | gold


class Plan(BaseModel):
    """A validated DAG of actions. Construction *is* structural validation:

    - unique action ids
    - dependencies reference existing actions
    - no cycles
    - every `$var` reference is produced by a (transitive) dependency
    - step count within budget
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    instruction: str
    instruction_hash: str = ""
    created_at: str = ""
    planner: PlannerInfo = Field(default_factory=PlannerInfo)
    actions: list[Action] = Field(min_length=1)
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def _validate_dag(self) -> Plan:
        if not self.instruction_hash:
            object.__setattr__(self, "instruction_hash", hash_instruction(self.instruction))
        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(timezone.utc).isoformat())

        ids = [a.action_id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate action_id in plan")
        if len(self.actions) > self.budget.max_steps:
            raise ValueError(
                f"plan has {len(self.actions)} actions, budget allows {self.budget.max_steps}"
            )
        known = set(ids)
        for a in self.actions:
            for dep in a.depends_on:
                if dep not in known:
                    raise ValueError(f"{a.action_id} depends on unknown action {dep!r}")
                if dep == a.action_id:
                    raise ValueError(f"{a.action_id} depends on itself")

        # Cycle check + topological order (stdlib does the work).
        ts = TopologicalSorter({a.action_id: set(a.depends_on) for a in self.actions})
        try:
            order = list(ts.static_order())
        except CycleError as e:
            raise ValueError(f"dependency cycle in plan: {e.args[1]}") from e
        object.__setattr__(self, "_topo_order", order)

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
        object.__setattr__(self, "_ancestors", ancestors)

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

    # -- accessors ---------------------------------------------------------

    @property
    def topo_order(self) -> list[str]:
        return list(self._topo_order)

    @property
    def ancestors(self) -> dict[str, set[str]]:
        return {k: set(v) for k, v in self._ancestors.items()}

    def action(self, action_id: str) -> Action:
        for a in self.actions:
            if a.action_id == action_id:
                return a
        raise KeyError(action_id)

    def producer_of(self, var: str) -> Action | None:
        for a in self.actions:
            if a.produces == var:
                return a
        return None

    def canonical(self) -> list[dict[str, Any]]:
        """Order-independent comparison form for the whole plan."""
        return sorted(
            (a.canonical() for a in self.actions),
            key=lambda d: json.dumps(d, sort_keys=True),
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.canonical(), sort_keys=True).encode()
        ).hexdigest()[:16]

    def verb_sequence(self) -> list[str]:
        return [self.action(aid).verb for aid in self.topo_order]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def hash_instruction(instruction: str) -> str:
    return "sha256:" + hashlib.sha256(instruction.strip().encode()).hexdigest()


def normalize_path_str(s: str) -> str:
    """Normalise a path *as a string*, without touching the filesystem.

    Used only for comparison (dataset gold plans are written with `~`, a
    planner may emit an absolute path). Never used for a safety decision —
    those go through PathPolicy, which does a real `resolve()`.
    """
    import os

    t = s.strip().replace("\\", "/")
    home = str(os.path.expanduser("~")).replace("\\", "/").rstrip("/")
    for prefix in (home, "/Users/me", "/home/me", "C:/Users/me"):
        if prefix and t.lower().startswith(prefix.lower()):
            t = "~" + t[len(prefix) :]
    while "//" in t:
        t = t.replace("//", "/")
    if len(t) > 1 and t.endswith("/"):
        t = t[:-1]
    return t


def _looks_like_path(s: str) -> bool:
    return ("/" in s or "\\" in s) or s.startswith("~")


def _canon_value(v: Any) -> Any:
    if isinstance(v, str):
        return normalize_path_str(v) if _looks_like_path(v) else v
    if isinstance(v, dict):
        return {k: _canon_value(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [_canon_value(x) for x in v]
    return v
