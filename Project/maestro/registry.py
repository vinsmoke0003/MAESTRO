"""The closed verb registry.

A verb that is not registered here does not exist: the planner cannot invent
capabilities, because an unknown verb is a hard plan rejection before anything
executes (threat T6, docs/06-SAFETY-SPEC.md §1).

Each verb declares:
  - a Pydantic args model  -> typed validation of every argument
  - a base risk tier       -> the floor the deterministic scorer starts from
  - reversibility          -> irreversible verbs can never score below R3
  - hard_blocked           -> verbs that exist only to be refused, so that
                              refusal is a *tested behavior*, not a missing case
  - path_args / net_write  -> what the deterministic scorer needs to inspect

The registry is populated by the executor modules at import time
(`import maestro.executor` registers every verb below).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from maestro.ir import Risk


class RegistryError(Exception):
    """Unknown verb or invalid args — always a plan rejection, never a warning."""


@dataclass(frozen=True)
class VerbSpec:
    verb: str
    args_model: type[BaseModel]
    base_risk: Risk
    reversible: bool
    hard_blocked: bool = False
    description: str = ""
    # names of args that hold filesystem paths — the safety scorer inspects these
    path_args: tuple[str, ...] = field(default_factory=tuple)
    # True when the verb writes to something outside this machine (docs/06 §2)
    network_write: bool = False
    # args whose value must never be derived from untrusted content (docs/02 §6 rule 3)
    sensitive_args: tuple[str, ...] = field(default_factory=tuple)
    # coarse capability group, used by profiles and by the dataset's intent map
    category: str = "misc"

    @property
    def blocked_reason(self) -> str:
        return f"{self.verb} is hard-blocked by policy; no override exists"


_REGISTRY: dict[str, VerbSpec] = {}


def register(spec: VerbSpec) -> VerbSpec:
    if spec.verb in _REGISTRY:
        raise ValueError(f"verb {spec.verb!r} registered twice")
    _REGISTRY[spec.verb] = spec
    return spec


def get(verb: str) -> VerbSpec:
    try:
        return _REGISTRY[verb]
    except KeyError:
        raise RegistryError(f"unknown verb {verb!r} — not in the closed registry") from None


def known_verbs() -> list[str]:
    return sorted(_REGISTRY)


def plannable_verbs() -> list[str]:
    """Verbs the planner is allowed to emit — hard-blocked ones are not advertised."""
    return sorted(v for v, s in _REGISTRY.items() if not s.hard_blocked)


def hard_blocked_verbs() -> list[str]:
    return sorted(v for v, s in _REGISTRY.items() if s.hard_blocked)


def by_category() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v, s in sorted(_REGISTRY.items()):
        out.setdefault(s.category, []).append(v)
    return out


def validate_args(verb: str, args: dict) -> BaseModel:
    """Validate raw args against the verb's schema. Raises RegistryError on failure."""
    spec = get(verb)
    try:
        return spec.args_model.model_validate(args)
    except ValidationError as e:
        raise RegistryError(f"invalid args for {verb}: {e}") from e


def snapshot() -> dict[str, dict]:
    """Machine-readable registry dump — goes into the dataset card and the report."""
    return {
        v: {
            "risk": str(s.base_risk),
            "reversible": s.reversible,
            "hard_blocked": s.hard_blocked,
            "category": s.category,
            "description": s.description,
            "args": {
                name: str(f.annotation) for name, f in s.args_model.model_fields.items()
            },
        }
        for v, s in sorted(_REGISTRY.items())
    }


def _reset_for_tests() -> None:  # pragma: no cover - test helper
    _REGISTRY.clear()
