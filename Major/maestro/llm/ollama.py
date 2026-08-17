"""Local LLM access via Ollama's REST API.

stdlib-only (urllib) — no extra dependency for one POST endpoint.

Two design points that matter beyond convenience:

- `schema` is passed as Ollama's `format` field -> CONSTRAINED DECODING.
  The model cannot emit JSON that violates the schema, which turns "usually
  valid" into "valid by construction" (docs/03 §2).
- `LLMClient` is a Protocol so the planner is testable with a fake and
  swappable to any provider later (docs/03 §3: provider-agnostic interface).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"


class LLMError(Exception):
    """Provider unreachable, model missing, or malformed response."""


@runtime_checkable
class LLMClient(Protocol):
    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str: ...


class OllamaClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 180.0,
        temperature: float = 0.1,  # planning wants determinism, not creativity
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.temperature = temperature

    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if schema is not None:
            payload["format"] = schema  # constrained decoding

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise LLMError(
                f"cannot reach Ollama at {self.base_url} — is `ollama serve` running? ({e})"
            ) from e
        except json.JSONDecodeError as e:
            raise LLMError(f"malformed response from Ollama: {e}") from e

        try:
            return body["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected Ollama response shape: {body!r:.200}") from e
