"""The L0 data model — one SQLite file, four tables (docs/02-ARCHITECTURE.md §8).

Everything MAESTRO remembers lives here:

    episodes    what was asked, what was planned, what happened
    audit_log   the hash-chained, tamper-evident record of every event
    preferences learned user defaults ("invoices go to Documents/Finance")
    undo_stack  durable inverse actions, so undo survives a process restart

They share one database file on purpose. NFR-10 requires that every executed
action be traceable to the instruction that caused it, and that traceability is
a join — which only works if the rows are in the same database.

`migrate()` is additive and idempotent: it adds missing columns to an existing
database rather than recreating it, so an installation that has been recording
episodes since v0.2 keeps its history.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Columns beyond the v0.2 set. Each feeds a metric in docs/07-EVALUATION.md:
# latency percentiles (NFR-03/04), consent-vs-gate agreement (SCR/FCR),
# cross-platform equivalence (NFR-09), and per-model comparison (M0-M5).
_EPISODE_COLUMNS: dict[str, str] = {
    "input_mode": "TEXT",        # text | voice  -> voice-vs-text comparison
    "intent": "TEXT",            # NLP stage 1 output
    "intent_conf": "REAL",       # confidence, for the clarification threshold
    "consent": "TEXT",           # auto | approved | typed | denied (what happened)
    "steps_ok": "INTEGER",
    "steps_total": "INTEGER",
    "plan_ms": "INTEGER",        # planning latency
    "exec_ms": "INTEGER",        # execution latency
    "platform": "TEXT",          # darwin | win32 -> differential analysis
    # The human label the dataset rule requires. NULL means "not yet reviewed",
    # and export refuses to call an unreviewed episode training-ready
    # (docs/05 §2, and the known issue documented in Major/README.md).
    "expected_behavior": "TEXT",  # execute_auto|execute_with_consent|clarify|refuse
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    instruction TEXT NOT NULL,
    plan_json   TEXT NOT NULL,
    plan_risk   TEXT,
    gate        TEXT,              -- what policy REQUIRED
    status      TEXT NOT NULL,     -- completed|blocked|denied|failed|rolled_back
    detail      TEXT,
    planner     TEXT               -- model that produced the plan
);

CREATE TABLE IF NOT EXISTS preferences (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    confidence  REAL DEFAULT 1.0,
    learned_from TEXT,             -- episode_id or 'user' — never an opaque guess
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS undo_stack (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id  INTEGER REFERENCES episodes(episode_id),
    action_id   TEXT NOT NULL,
    verb        TEXT NOT NULL,
    undo_json   TEXT NOT NULL,
    applied     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status);
CREATE INDEX IF NOT EXISTS idx_undo_episode   ON undo_stack(episode_id, applied);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the store, creating and migrating it if needed."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any missing episode columns. Idempotent; returns what it added."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
    added = []
    for col, decl in _EPISODE_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE episodes ADD COLUMN {col} {decl}")
            added.append(col)
    # audit_log is created by AuditLog itself; link it to episodes when present.
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "audit_log" in tables:
        acols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)")}
        if "episode_id" not in acols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN episode_id INTEGER")
            added.append("audit_log.episode_id")
    conn.commit()
    return added
