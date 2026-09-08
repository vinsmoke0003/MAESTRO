"""Semantic exemplar retrieval (FR-51) — "the plans that worked for tasks like
this one", fed back into the planner as few-shot context.

Two backends behind one interface:

* **ChromaDB + sentence-transformers**, if installed. Real embeddings, the
  configuration named in docs/03.
* **A hashing bag-of-words store**, always available. TF-weighted character and
  word n-grams in a fixed-dimension sparse vector, cosine similarity. It is not
  as good as a neural embedding, and it needs no model download, no ~90 MB
  dependency and no first-run latency.

The fallback is not a stub — it is a working retriever, and the A4 ablation
("memory / retrieval removed") is measured against whichever backend is active.
Which one ran is recorded in the results so the number is interpretable.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DIM = 4096


@dataclass(frozen=True)
class Exemplar:
    doc_id: str
    instruction: str
    plan: dict
    intent: str = ""
    score: float = 0.0


def _tokens(text: str) -> list[str]:
    t = text.lower()
    words = re.findall(r"[a-z0-9]+", t)
    grams = [t[i:i + 4] for i in range(max(0, len(t) - 3))]
    return words + [w for w in grams if w.strip()]


def _vector(text: str) -> dict[int, float]:
    counts = Counter(hash(tok) % DIM for tok in _tokens(text))
    norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
    return {k: v / norm for k, v in counts.items()}


def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


_SCHEMA = """
CREATE TABLE IF NOT EXISTS exemplars (
    doc_id      TEXT PRIMARY KEY,
    instruction TEXT NOT NULL,
    intent      TEXT,
    plan_json   TEXT NOT NULL
);
"""


class HashingStore:
    """Zero-dependency exemplar store. Vectors are recomputed on load, which is
    fine at the scale this holds (hundreds of exemplars, not millions)."""

    backend = "hashing"

    def __init__(self, path: str | Path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(p / "exemplars.db") if p.is_dir()
                                     else str(p), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._cache: dict[str, dict[int, float]] = {}

    def add(self, doc_id: str, instruction: str, plan: dict, intent: str = "") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO exemplars (doc_id, instruction, intent, plan_json)"
            " VALUES (?,?,?,?)",
            (doc_id, instruction, intent, json.dumps(plan)),
        )
        self._conn.commit()
        self._cache[doc_id] = _vector(instruction)

    def add_many(self, rows: list[tuple[str, str, dict, str]]) -> int:
        for doc_id, instruction, plan, intent in rows:
            self.add(doc_id, instruction, plan, intent)
        return len(rows)

    def query(self, text: str, k: int = 3, intent: str | None = None) -> list[Exemplar]:
        q = _vector(text)
        sql = "SELECT doc_id, instruction, intent, plan_json FROM exemplars"
        params: tuple = ()
        if intent:
            sql += " WHERE intent = ?"
            params = (intent,)
        out: list[Exemplar] = []
        for doc_id, instruction, ex_intent, plan_json in self._conn.execute(sql, params):
            vec = self._cache.get(doc_id) or _vector(instruction)
            self._cache[doc_id] = vec
            out.append(Exemplar(doc_id, instruction, json.loads(plan_json),
                                ex_intent or "", _cosine(q, vec)))
        out.sort(key=lambda e: -e.score)
        return [e for e in out[:k] if e.score > 0.05]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM exemplars").fetchone()[0])

    def clear(self) -> None:
        self._conn.execute("DELETE FROM exemplars")
        self._conn.commit()
        self._cache.clear()

    def close(self) -> None:
        self._conn.close()


class ChromaStore:  # pragma: no cover - optional dependency
    """ChromaDB backend. Same interface, real embeddings."""

    backend = "chroma"

    def __init__(self, path: str | Path, collection: str = "workflows"):
        import chromadb

        Path(path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._col = self._client.get_or_create_collection(collection)

    def add(self, doc_id: str, instruction: str, plan: dict, intent: str = "") -> None:
        self._col.upsert(ids=[doc_id], documents=[instruction],
                         metadatas=[{"plan": json.dumps(plan), "intent": intent}])

    def add_many(self, rows: list[tuple[str, str, dict, str]]) -> int:
        if not rows:
            return 0
        self._col.upsert(
            ids=[r[0] for r in rows],
            documents=[r[1] for r in rows],
            metadatas=[{"plan": json.dumps(r[2]), "intent": r[3]} for r in rows],
        )
        return len(rows)

    def query(self, text: str, k: int = 3, intent: str | None = None) -> list[Exemplar]:
        where = {"intent": intent} if intent else None
        res = self._col.query(query_texts=[text], n_results=k, where=where)
        out = []
        for i, doc_id in enumerate(res.get("ids", [[]])[0]):
            meta = res["metadatas"][0][i]
            dist = res.get("distances", [[0]])[0][i]
            out.append(Exemplar(doc_id, res["documents"][0][i],
                                json.loads(meta.get("plan", "{}")),
                                meta.get("intent", ""), 1.0 - float(dist)))
        return out

    def count(self) -> int:
        return int(self._col.count())

    def clear(self) -> None:
        self._col.delete(where={})

    def close(self) -> None:
        pass


def open_store(path: str | Path, prefer_chroma: bool = True):
    """Chroma if it imports, hashing store otherwise. Never raises."""
    if prefer_chroma:
        try:
            return ChromaStore(path)
        except Exception:
            pass
    return HashingStore(path)
