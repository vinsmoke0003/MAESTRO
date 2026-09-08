"""Local LLM access via Ollama's REST API.

stdlib-only (urllib) — no extra dependency for one POST endpoint.

The design point that matters beyond convenience: `schema` is passed as
Ollama's `format` field, which is **constrained decoding**. The model cannot
emit JSON that violates the schema, which turns "usually valid" into "valid by
construction" (docs/03 §2) and removes a whole category of retry logic.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from maestro.llm.base import LLMError

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"

# Per-process memo of `available()`. Ollama does not start or stop in the middle
# of a benchmark run, and re-probing per task cost more than the run itself.
_AVAILABILITY: dict[str, bool] = {}


def reset_availability_cache() -> None:
    """For tests, and for a long-lived process that wants to re-probe."""
    _AVAILABILITY.clear()


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

    # -- availability ------------------------------------------------------

    def available(self, *, use_cache: bool = True) -> bool:
        """Is anything listening?

        A raw socket connect rather than an HTTP GET, and memoised per process.
        Both matter: on Windows, `urlopen("http://localhost:11434/...")` against
        a closed port costs ~4 seconds — `localhost` resolves to ::1 first and
        the IPv6 attempt has to time out before IPv4 is tried. The evaluation
        harness constructs a pipeline per task, so that probe alone was
        dominating a 100-task run. Connecting to an explicit IPv4 address with a
        short timeout turns 4s into ~2ms.
        """
        if use_cache and self.base_url in _AVAILABILITY:
            return _AVAILABILITY[self.base_url]
        ok = self._probe()
        _AVAILABILITY[self.base_url] = ok
        return ok

    def _probe(self) -> bool:
        import socket
        import urllib.parse

        parts = urllib.parse.urlparse(self.base_url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if host in ("localhost", "::1"):
            host = "127.0.0.1"
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False

    def models(self) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=5.0) as r:
                body = json.loads(r.read())
            return [m.get("name", "") for m in body.get("models", [])]
        except Exception:
            return []

    # -- the call ----------------------------------------------------------

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
        except urllib.error.HTTPError as e:
            raise LLMError(f"Ollama returned HTTP {e.code}: {e.read()[:300]!r}") from e
        except urllib.error.URLError as e:
            raise LLMError(
                f"cannot reach Ollama at {self.base_url} — is `ollama serve` running? ({e})"
            ) from e
        except json.JSONDecodeError as e:
            raise LLMError(f"malformed response from Ollama: {e}") from e

        try:
            return body["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected Ollama response shape: {str(body)[:200]!r}") from e
