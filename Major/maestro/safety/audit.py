"""Hash-chained append-only audit log (docs/02-ARCHITECTURE.md §8).

Each row's hash covers the previous row's hash plus the canonical row body,
so editing or deleting any historical row breaks verification of every row
after it. ~40 lines buys the property "we can prove the log wasn't edited".
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64

EVENTS = {
    "PROPOSED",
    "GATED",
    "APPROVED",
    "DENIED",
    "BLOCKED",
    "EXECUTED",
    "FAILED",
    "UNDONE",
    "INJECTION_DETECTED",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    plan_id    TEXT,
    action_id  TEXT,
    verb       TEXT,
    event      TEXT NOT NULL,
    detail     TEXT,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);
"""


class AuditLog:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def append(
        self,
        event: str,
        *,
        plan_id: str | None = None,
        action_id: str | None = None,
        verb: str | None = None,
        detail: str = "",
    ) -> str:
        if event not in EVENTS:
            raise ValueError(f"unknown audit event {event!r}")
        prev = self._last_hash()
        ts = datetime.now(timezone.utc).isoformat()
        body = json.dumps(
            [ts, plan_id, action_id, verb, event, detail], separators=(",", ":")
        )
        h = hashlib.sha256((prev + body).encode()).hexdigest()
        self._conn.execute(
            "INSERT INTO audit_log (ts, plan_id, action_id, verb, event, detail, prev_hash, hash)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ts, plan_id, action_id, verb, event, detail, prev, h),
        )
        self._conn.commit()
        return h

    def verify(self) -> bool:
        """Recompute the whole chain. False means the log was tampered with."""
        prev = GENESIS
        for ts, plan_id, action_id, verb, event, detail, prev_hash, h in self._conn.execute(
            "SELECT ts, plan_id, action_id, verb, event, detail, prev_hash, hash"
            " FROM audit_log ORDER BY seq"
        ):
            if prev_hash != prev:
                return False
            body = json.dumps(
                [ts, plan_id, action_id, verb, event, detail], separators=(",", ":")
            )
            if hashlib.sha256((prev + body).encode()).hexdigest() != h:
                return False
            prev = h
        return True

    def _last_hash(self) -> str:
        row = self._conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        return row[0] if row else GENESIS

    def close(self) -> None:
        self._conn.close()
