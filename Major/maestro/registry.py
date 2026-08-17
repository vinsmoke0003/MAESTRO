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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Type

from pydantic import BaseModel, ValidationError

from maestro.ir import Risk


class RegistryError(Exception):
    """Unknown verb or invalid args — always a plan rejection, never a warning."""


@dataclass(frozen=True)
class VerbSpec:
    verb: str
    args_model: Type[BaseModel]
    base_risk: Risk
    reversible: bool
    hard_blocked: bool = False
    description: str = ""
    # names of args that hold filesystem paths — the safety scorer inspects these
    path_args: tuple[str, ...] = field(default_factory=tuple)


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


def validate_args(verb: str, args: dict) -> BaseModel:
    """Validate raw args against the verb's schema. Raises RegistryError on failure."""
    spec = get(verb)
    try:
        return spec.args_model.model_validate(args)
    except ValidationError as e:
        raise RegistryError(f"invalid args for {verb}: {e}") from e
