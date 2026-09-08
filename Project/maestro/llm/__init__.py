"""LLM provider layer. Swapping a model is a config change, not a code change."""

from maestro.llm.base import (
    CachingClient,
    LLMClient,
    LLMError,
    ResponseCache,
    Usage,
)
from maestro.llm.fake import EchoClient, FailingClient, ScriptedClient
from maestro.llm.ollama import OllamaClient
from maestro.llm.openai_compat import OpenAICompatClient
from maestro.llm.router import Backend, describe, pick

__all__ = [
    "CachingClient",
    "LLMClient",
    "LLMError",
    "ResponseCache",
    "Usage",
    "EchoClient",
    "FailingClient",
    "ScriptedClient",
    "OllamaClient",
    "OpenAICompatClient",
    "Backend",
    "describe",
    "pick",
]
