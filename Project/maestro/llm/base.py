"""Provider-agnostic LLM interface + a response cache.

docs/03 §3: "One provider-agnostic `LLMProvider` interface; swapping models must
be a config change, not a code change — you will do this dozens of times during
evaluation." And: "Cache every cloud response by hash(prompt+model+params) in
SQLite. Re-running an evaluation should not re-spend quota, and it makes results
reproducible."

Both of those are here. The cache is on by default for every backend, not just
cloud ones, because a cached local run is also a *reproducible* local run — the
same 3,000-run evaluation matrix replays identically.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class LLMError(Exception):
    """Provider unreachable, model missing, or malformed response."""


@runtime_checkable
class LLMClient(Protocol):
    model: str

    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str: ...


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    total_ms: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)

    def record(self, ms: float, cached: bool) -> None:
        self.calls += 1
        self.total_ms += ms
        self.latencies_ms.append(ms)
        if cached:
            self.cache_hits += 1

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        xs = sorted(self.latencies_ms)
        k = min(len(xs) - 1, max(0, int(round((p / 100) * (len(xs) - 1)))))
        return xs[k]

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "p50_ms": round(self.percentile(50), 1),
            "p95_ms": round(self.percentile(95), 1),
            "total_ms": round(self.total_ms, 1),
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key      TEXT PRIMARY KEY,
    model    TEXT NOT NULL,
    response TEXT NOT NULL,
    ts       REAL NOT NULL
);
"""


class ResponseCache:
    def __init__(self, db_path: str | Path):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @staticmethod
    def key(model: str, system: str, user: str, schema: dict | None, temperature: float) -> str:
        blob = json.dumps(
            [model, system, user, schema, temperature], sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT response FROM llm_cache WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def put(self, key: str, model: str, response: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO llm_cache (key, model, response, ts) VALUES (?,?,?,?)",
            (key, model, response, time.time()),
        )
        self._conn.commit()

    def size(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0])

    def clear(self) -> None:
        self._conn.execute("DELETE FROM llm_cache")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class CachingClient:
    """Wraps any LLMClient with the response cache and usage accounting."""

    def __init__(self, inner: LLMClient, cache: ResponseCache | None,
                 temperature: float = 0.1):
        self.inner = inner
        self.cache = cache
        self.temperature = temperature
        self.usage = Usage()

    @property
    def model(self) -> str:
        return self.inner.model

    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str:
        key = ResponseCache.key(self.model, system, user, schema, self.temperature)
        t0 = time.perf_counter()
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                self.usage.record((time.perf_counter() - t0) * 1000, cached=True)
                return hit
        out = self.inner.chat(system, user, schema=schema)
        self.usage.record((time.perf_counter() - t0) * 1000, cached=False)
        if self.cache is not None:
            self.cache.put(key, self.model, out)
        return out
