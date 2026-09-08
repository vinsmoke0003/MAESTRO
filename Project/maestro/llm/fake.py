"""Deterministic in-process clients — tests, CI, and the offline demo.

`ScriptedClient` replays a fixed list of responses. Every planner test uses it,
which is why the planner test suite runs in milliseconds on a machine with no
Ollama, no GPU and no network — a property the CI workflow depends on.

`FailingClient` simulates an outage, so the "LLM unavailable" path is tested
rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from maestro.llm.base import LLMError


@dataclass
class ScriptedClient:
    """Returns `responses` in order; repeats the last one once exhausted."""

    responses: list[str] = field(default_factory=list)
    model: str = "scripted"
    calls: list[tuple[str, str, dict | None]] = field(default_factory=list)

    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str:
        self.calls.append((system, user, schema))
        if not self.responses:
            raise LLMError("ScriptedClient has no responses configured")
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx]


@dataclass
class FailingClient:
    model: str = "failing"
    message: str = "simulated provider outage"

    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str:
        raise LLMError(self.message)


@dataclass
class EchoClient:
    """Returns the user prompt back. Useful for prompt-construction tests."""

    model: str = "echo"

    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str:
        return user
