"""Episodic memory — the substrate of the learning loop (docs/02 §8, L0).

Every interaction is recorded as (instruction, plan, outcome). This is not
just history: successful episodes ARE future fine-tuning pairs. The loop:

    use MAESTRO -> episodes accumulate -> export_dataset() -> human review
    -> LoRA fine-tune (docs/05) -> better planner -> use MAESTRO ...

That is how the system "learns everything you do" over time: not one big
training run, but a flywheel fed by real usage. `export_dataset` emits only
successful, consented episodes — a denied or failed plan is not an example
worth imitating (it still stays in the table for failure analysis).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    instruction TEXT NOT NULL,
    plan_json   TEXT NOT NULL,
    plan_risk   TEXT,
    gate        TEXT,
    status      TEXT NOT NULL,     -- completed | blocked | denied | failed | rolled_back
    detail      TEXT,
    planner     TEXT               -- model that produced the plan
);
"""


class EpisodeStore:
    def __init__(self, db_path: str | Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record(
        self,
        *,
        instruction: str,
        plan_json: str,
        plan_risk: str,
        gate: str,
        status: str,
        detail: str = "",
        planner: str = "",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO episodes (ts, instruction, plan_json, plan_risk, gate,"
            " status, detail, planner) VALUES (?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                instruction,
                plan_json,
                plan_risk,
                gate,
                status,
                detail,
                planner,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def export_dataset(self, out_path: str | Path) -> int:
        """Write successful episodes as JSONL training pairs (docs/05 §2).

        Each line: {"instruction": ..., "plan": {...}, "source": "episode"}.
        These are CANDIDATES — the dataset rule still applies: nothing enters
        training unverified by a human.
        """
        rows = self._conn.execute(
            "SELECT instruction, plan_json FROM episodes WHERE status = 'completed'"
            " ORDER BY episode_id"
        ).fetchall()
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            for instruction, plan_json in rows:
                f.write(json.dumps({
                    "instruction": instruction,
                    "plan": json.loads(plan_json),
                    "source": "episode",
                    "verified_by": None,       # human review pending — required
                }) + "\n")
        return len(rows)

    def stats(self) -> dict[str, int]:
        return dict(self._conn.execute(
            "SELECT status, COUNT(*) FROM episodes GROUP BY status"
        ).fetchall())

    def close(self) -> None:
        self._conn.close()
