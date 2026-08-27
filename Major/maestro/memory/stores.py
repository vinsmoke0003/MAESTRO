"""Preference and undo stores (docs/02-ARCHITECTURE.md §8, L0).

Both are deliberately dumb tables with no inference in them. A preference is
written when the user states or confirms one — never guessed from a single
observation, because a wrong learned path is exactly the failure this project
exists to prevent (docs/05 §1, "never guess a path").
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PreferenceStore:
    """Learned user defaults, each with provenance the user can inspect."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def set(self, key: str, value: str, *, confidence: float = 1.0,
            learned_from: str = "user") -> None:
        self._conn.execute(
            "INSERT INTO preferences (key, value, confidence, learned_from, updated_at)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
            " confidence=excluded.confidence, learned_from=excluded.learned_from,"
            " updated_at=excluded.updated_at",
            (key, value, confidence, learned_from, _now()),
        )
        self._conn.commit()

    def get(self, key: str, *, min_confidence: float = 0.0) -> str | None:
        row = self._conn.execute(
            "SELECT value, confidence FROM preferences WHERE key = ?", (key,)
        ).fetchone()
        if row is None or row[1] < min_confidence:
            return None
        return row[0]

    def all(self) -> list[dict]:
        return [
            {"key": k, "value": v, "confidence": c, "learned_from": lf, "updated_at": u}
            for k, v, c, lf, u in self._conn.execute(
                "SELECT key, value, confidence, learned_from, updated_at"
                " FROM preferences ORDER BY key")
        ]

    def forget(self, key: str) -> bool:
        """FR-53: the user can delete anything in memory."""
        cur = self._conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
        self._conn.commit()
        return cur.rowcount > 0


class UndoStack:
    """Durable inverse actions (FR-29).

    The orchestrator keeps an in-process stack for rollback *during* a run.
    This table is what makes undo survive the process exiting, so "undo that"
    still works in the next session.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def push(self, *, episode_id: int | None, action_id: str, verb: str,
             undo_data: dict) -> int:
        cur = self._conn.execute(
            "INSERT INTO undo_stack (episode_id, action_id, verb, undo_json, created_at)"
            " VALUES (?,?,?,?,?)",
            (episode_id, action_id, verb, json.dumps(undo_data), _now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def pending(self, episode_id: int | None = None) -> list[dict]:
        """Un-applied undos, newest first — the order they must be replayed in."""
        sql = ("SELECT id, episode_id, action_id, verb, undo_json FROM undo_stack"
               " WHERE applied = 0")
        args: tuple = ()
        if episode_id is not None:
            sql += " AND episode_id = ?"
            args = (episode_id,)
        sql += " ORDER BY id DESC"
        return [
            {"id": i, "episode_id": e, "action_id": a, "verb": v,
             "undo_data": json.loads(j)}
            for i, e, a, v, j in self._conn.execute(sql, args)
        ]

    def last_episode(self) -> int | None:
        row = self._conn.execute(
            "SELECT episode_id FROM undo_stack WHERE applied = 0"
            " ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def mark_applied(self, undo_id: int) -> None:
        self._conn.execute("UPDATE undo_stack SET applied = 1 WHERE id = ?", (undo_id,))
        self._conn.commit()
