"""Hash-chained append-only audit log (docs/02-ARCHITECTURE.md §8).

Each row's hash covers the previous row's hash plus the canonical row body, so
editing or deleting any historical row breaks verification of every row after
it. ~60 lines buys the property "we can prove the log wasn't edited" — which is
the difference between the FR-27 claim and a print statement.

The AIR metric in docs/07 §2 is literally `verify()` returning True.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
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
    "CLARIFY",
    "BUDGET_EXCEEDED",
    "ABORTED",
    "PREVIEWED",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    episode_id TEXT,
    plan_id    TEXT,
    action_id  TEXT,
    verb       TEXT,
    args_json  TEXT,
    risk       TEXT,
    event      TEXT NOT NULL,
    detail     TEXT,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_plan ON audit_log(plan_id);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event);
"""

_FIELDS = ("ts", "episode_id", "plan_id", "action_id", "verb", "args_json", "risk",
           "event", "detail")


@dataclass(frozen=True)
class AuditRow:
    seq: int
    ts: str
    episode_id: str | None
    plan_id: str | None
    action_id: str | None
    verb: str | None
    args_json: str | None
    risk: str | None
    event: str
    detail: str
    prev_hash: str
    hash: str


class AuditLog:
    def __init__(self, db_path: str | Path):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- writing -----------------------------------------------------------

    def append(
        self,
        event: str,
        *,
        episode_id: str | None = None,
        plan_id: str | None = None,
        action_id: str | None = None,
        verb: str | None = None,
        args: dict | None = None,
        risk: str | None = None,
        detail: str = "",
    ) -> str:
        if event not in EVENTS:
            raise ValueError(f"unknown audit event {event!r}")
        prev = self.head()
        ts = datetime.now(timezone.utc).isoformat()
        args_json = json.dumps(args, sort_keys=True, separators=(",", ":")) if args else None
        row = (ts, episode_id, plan_id, action_id, verb, args_json, risk, event, detail)
        h = _row_hash(prev, row)
        self._conn.execute(
            "INSERT INTO audit_log (ts, episode_id, plan_id, action_id, verb, args_json,"
            " risk, event, detail, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (*row, prev, h),
        )
        self._conn.commit()
        return h

    # -- reading -----------------------------------------------------------

    def head(self) -> str:
        row = self._conn.execute(
            "SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def rows(self, plan_id: str | None = None) -> list[AuditRow]:
        sql = ("SELECT seq, ts, episode_id, plan_id, action_id, verb, args_json, risk,"
               " event, detail, prev_hash, hash FROM audit_log")
        params: tuple = ()
        if plan_id:
            sql += " WHERE plan_id = ?"
            params = (plan_id,)
        sql += " ORDER BY seq"
        return [AuditRow(*r) for r in self._conn.execute(sql, params)]

    def count(self, event: str | None = None) -> int:
        if event:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE event = ?", (event,)
            ).fetchone()[0])
        return int(self._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])

    # -- the property ------------------------------------------------------

    def verify(self) -> bool:
        """Recompute the whole chain. False means the log was tampered with."""
        return self.verify_detailed()[0]

    def verify_detailed(self) -> tuple[bool, int | None, str]:
        """(ok, first_bad_seq, message) — the harness reports the seq so a
        tampering demo can point at the exact row."""
        prev = GENESIS
        for r in self.rows():
            body = (r.ts, r.episode_id, r.plan_id, r.action_id, r.verb, r.args_json,
                    r.risk, r.event, r.detail)
            if r.prev_hash != prev:
                return False, r.seq, f"row {r.seq}: prev_hash does not match row {r.seq - 1}"
            if _row_hash(prev, body) != r.hash:
                return False, r.seq, f"row {r.seq}: body was edited after it was written"
            prev = r.hash
        return True, None, "chain intact"

    def close(self) -> None:
        self._conn.close()


def _row_hash(prev: str, row: tuple) -> str:
    body = json.dumps(list(row), separators=(",", ":"))
    return hashlib.sha256((prev + body).encode()).hexdigest()
