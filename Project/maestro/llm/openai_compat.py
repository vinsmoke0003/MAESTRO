"""OpenAI-compatible chat client — for the M5 / B4 frontier baselines only.

docs/03 §3: nothing in the critical path may require a cloud provider. This
client exists so the evaluation can report a capability ceiling, and it is
never selected by `router.pick()` unless `MAESTRO_LLM=openai` is set explicitly
and a key is present.

Works against any provider exposing `/chat/completions`: Groq, OpenRouter,
Cerebras, Together, Gemini's OpenAI-compat endpoint, a local vLLM. Set:

    MAESTRO_LLM=openai
    MAESTRO_LLM_BASE=https://api.groq.com/openai/v1
    MAESTRO_MODEL=llama-3.3-70b-versatile
    MAESTRO_API_KEY=...          # your key; never committed, see .env.example

Privacy warning that belongs in the report (docs/03 §3): a desktop agent's
prompts contain file paths and folder structures. Sending those to a free tier
is a real privacy leak, and it is exactly the argument for the local-first
default.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from maestro.llm.base import LLMError


class OpenAICompatClient:
    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "",
        timeout_s: float = 120.0,
        temperature: float = 0.1,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.temperature = temperature

    def available(self) -> bool:
        return bool(self.api_key and self.base_url)

    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "stream": False,
        }
        if schema is not None:
            # Providers differ: most honour json_object, some honour json_schema.
            # We ask for json_schema and fall back to json_object on rejection.
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "action_ir", "schema": schema, "strict": False},
            }

        try:
            return self._post(payload)
        except LLMError as first:
            if schema is None or "json_schema" not in str(first):
                raise
            payload["response_format"] = {"type": "json_object"}
            return self._post(payload)

    def _post(self, payload: dict) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"provider returned HTTP {e.code}: {e.read()[:300]!r}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"cannot reach {self.base_url}: {e}") from e
        except json.JSONDecodeError as e:
            raise LLMError(f"malformed response: {e}") from e

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response shape: {str(body)[:200]!r}") from e
