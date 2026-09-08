"""Provider router — picks a backend from configuration, never from code.

`MAESTRO_LLM` selects it:

    auto    (default) use Ollama if it is actually reachable, else None
    ollama  force Ollama; error if unreachable
    openai  force an OpenAI-compatible endpoint (baselines only)
    none    force no LLM at all

Returning `None` is a first-class outcome, not a failure. MAESTRO ships a
deterministic rule-based planner (`maestro.planner.rulebased`) that covers the
P0 verb set, so the whole pipeline — NLP, safety, dry run, consent, execution,
audit — runs end to end on a laptop with nothing installed. The LLM upgrades
plan *coverage*; it is not load-bearing for any safety property, which is the
point docs/02 §1 principle 1 is making.
"""

from __future__ import annotations

from dataclasses import dataclass

from maestro.config import Settings, settings
from maestro.llm.base import CachingClient, LLMClient, LLMError, ResponseCache
from maestro.llm.ollama import DEFAULT_BASE_URL, OllamaClient
from maestro.llm.openai_compat import OpenAICompatClient


@dataclass(frozen=True)
class Backend:
    client: LLMClient | None
    name: str  # ollama | openai | none
    model: str
    note: str = ""

    @property
    def available(self) -> bool:
        return self.client is not None


def pick(cfg: Settings | None = None, *, cache: bool = True) -> Backend:
    cfg = cfg or settings()
    choice = (cfg.backend or "auto").lower()

    def wrap(inner: LLMClient, name: str, note: str = "") -> Backend:
        client: LLMClient = inner
        if cache:
            client = CachingClient(inner, ResponseCache(cfg.cache_db))
        return Backend(client, name, inner.model, note)

    if choice == "none":
        return Backend(None, "none", cfg.model, "MAESTRO_LLM=none")

    if choice == "openai":
        c = OpenAICompatClient(cfg.model, cfg.llm_base_url, cfg.api_key)
        if not c.available():
            raise LLMError(
                "MAESTRO_LLM=openai but MAESTRO_API_KEY / MAESTRO_LLM_BASE are not set"
            )
        return wrap(c, "openai", f"cloud baseline via {cfg.llm_base_url}")

    ollama = OllamaClient(cfg.model, cfg.llm_base_url if "11434" in cfg.llm_base_url
                          else DEFAULT_BASE_URL)

    if choice == "ollama":
        if not ollama.available():
            raise LLMError(
                f"MAESTRO_LLM=ollama but nothing is listening at {ollama.base_url}. "
                "Start it with `ollama serve`."
            )
        return wrap(ollama, "ollama", f"local model {cfg.model}")

    # auto
    if ollama.available():
        installed = ollama.models()
        note = f"local model {cfg.model}"
        if installed and cfg.model not in installed:
            note += f" (not pulled yet; available: {', '.join(installed[:4])})"
        return wrap(ollama, "ollama", note)

    return Backend(None, "none", cfg.model,
                   "no LLM reachable — the deterministic rule planner will be used")


def describe(cfg: Settings | None = None) -> str:
    try:
        b = pick(cfg, cache=False)
    except LLMError as e:
        return f"LLM: unavailable ({e})"
    if not b.available:
        return f"LLM: none — {b.note}"
    return f"LLM: {b.name} · model={b.model} — {b.note}"
