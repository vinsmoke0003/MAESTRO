"""Central configuration. Every tunable lives here or in an env var — never
inline in a module, because the evaluation harness has to sweep them.

Environment variables (all optional, all with working defaults):

    MAESTRO_HOME        state directory                 (default ~/.maestro)
    MAESTRO_WORKSPACE   the agent's own writable root   (default ~/maestro_workspace)
    MAESTRO_MODEL       planner model id                (default qwen2.5:7b-instruct-q4_K_M)
    MAESTRO_LLM         planner backend: auto|ollama|openai|none   (default auto)
    MAESTRO_LLM_BASE    base URL for the backend
    MAESTRO_API_KEY     key for an OpenAI-compatible cloud backend (baselines only)
    MAESTRO_BULK_N      bulk-operation threshold        (default 25)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


@dataclass(frozen=True)
class Settings:
    home: Path = field(default_factory=lambda: _env_path("MAESTRO_HOME", "~/.maestro"))
    workspace: Path = field(
        default_factory=lambda: _env_path("MAESTRO_WORKSPACE", "~/maestro_workspace")
    )

    # --- LLM -------------------------------------------------------------
    model: str = field(
        default_factory=lambda: os.environ.get("MAESTRO_MODEL", "qwen2.5:7b-instruct-q4_K_M")
    )
    backend: str = field(default_factory=lambda: os.environ.get("MAESTRO_LLM", "auto"))
    llm_base_url: str = field(
        default_factory=lambda: os.environ.get("MAESTRO_LLM_BASE", "http://127.0.0.1:11434")
    )
    api_key: str = field(default_factory=lambda: os.environ.get("MAESTRO_API_KEY", ""))

    # --- safety knobs (swept by the ablation harness) ---------------------
    bulk_n: int = field(default_factory=lambda: int(os.environ.get("MAESTRO_BULK_N", "25")))

    @property
    def audit_db(self) -> Path:
        return self.home / "audit.db"

    @property
    def episodes_db(self) -> Path:
        return self.home / "episodes.db"

    @property
    def cache_db(self) -> Path:
        return self.home / "llm_cache.db"

    @property
    def vector_dir(self) -> Path:
        return self.home / "vectors"

    @property
    def intent_model(self) -> Path:
        """Trained intent classifier artifact (training/train_intent.py)."""
        override = os.environ.get("MAESTRO_INTENT_MODEL")
        if override:
            return Path(override).expanduser()
        return Path(__file__).resolve().parent.parent / "models" / "intent" / "intent_clf.joblib"

    def ensure_dirs(self) -> Settings:
        self.home.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self


SETTINGS = Settings()


def settings() -> Settings:
    """Re-read the environment. Tests and the harness monkeypatch env vars."""
    return Settings()
