"""Episodic memory — the substrate of the learning loop (docs/02 §8, L0).

Every interaction is recorded as (instruction, plan, outcome). This is not
just history: reviewed episodes ARE future fine-tuning pairs. The loop:

    use MAESTRO -> episodes accumulate -> export_dataset() -> human review
    -> LoRA fine-tune (docs/05) -> better planner -> use MAESTRO ...

That is how the system "learns everything you do" over time: not one big
training run, but a flywheel fed by real usage.

**Refusals are training data too.** An unsafe instruction that the safety layer
blocked is the most valuable example in the set — it is the only thing that
teaches the model to refuse. Exporting only successes would train a planner
that has never seen a refusal, which is precisely backwards for a safety
project. So export emits blocked and denied episodes as well, each carrying
its outcome, and nothing is training-ready until a human has labeled its
`expected_behavior` (docs/05 §2).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from maestro.memory import schema

# How an outcome maps to the label a human should confirm. A suggestion only —
# the field stays NULL until a person actually reviews it.
_SUGGESTED_LABEL = {
    "blocked": "refuse",
    "denied": "refuse",
    "completed": None,      # execute_auto vs execute_with_consent: gate decides
    "failed": None,
    "rolled_back": None,
}

VALID_LABELS = {"execute_auto", "execute_with_consent", "clarify", "refuse"}


class EpisodeStore:
    def __init__(self, db_path: str | Path | sqlite3.Connection):
        self._conn = (db_path if isinstance(db_path, sqlite3.Connection)
                      else schema.connect(db_path))

    @property
    def conn(self) -> sqlite3.Connection:
        """Shared connection — PreferenceStore and UndoStack ride on this."""
        return self._conn

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
        input_mode: str = "text",
        intent: str | None = None,
        intent_conf: float | None = None,
        consent: str | None = None,
        steps_ok: int | None = None,
        steps_total: int | None = None,
        plan_ms: int | None = None,
        exec_ms: int | None = None,
        platform: str | None = None,
    ) -> int:
        import platform as _platform

        cur = self._conn.execute(
            "INSERT INTO episodes (ts, instruction, plan_json, plan_risk, gate,"
            " status, detail, planner, input_mode, intent, intent_conf, consent,"
            " steps_ok, steps_total, plan_ms, exec_ms, platform)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(), instruction, plan_json,
                plan_risk, gate, status, detail, planner, input_mode, intent,
                intent_conf, consent, steps_ok, steps_total, plan_ms, exec_ms,
                platform or _platform.system().lower(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def label(self, episode_id: int, expected_behavior: str) -> None:
        """Record the human review decision (docs/05 §2). The gate to training."""
        if expected_behavior not in VALID_LABELS:
            raise ValueError(
                f"expected_behavior must be one of {sorted(VALID_LABELS)}")
        self._conn.execute(
            "UPDATE episodes SET expected_behavior = ? WHERE episode_id = ?",
            (expected_behavior, episode_id),
        )
        self._conn.commit()

    def unlabeled(self, limit: int = 50) -> list[dict]:
        """The human-review queue, refusals first — they matter most."""
        rows = self._conn.execute(
            "SELECT episode_id, instruction, plan_risk, gate, status"
            " FROM episodes WHERE expected_behavior IS NULL"
            " ORDER BY CASE status WHEN 'blocked' THEN 0 WHEN 'denied' THEN 1"
            " ELSE 2 END, episode_id LIMIT ?", (limit,)).fetchall()
        return [
            {"episode_id": i, "instruction": t, "plan_risk": r, "gate": g,
             "status": s, "suggested": _SUGGESTED_LABEL.get(s)}
            for i, t, r, g, s in rows
        ]

    def export_dataset(self, out_path: str | Path) -> dict[str, int]:
        """Write episodes as JSONL training CANDIDATES (docs/05 §2).

        Emits successes AND refusals. `training_ready` is true only where a
        human has supplied `expected_behavior` — the dataset rule is absolute:
        nothing enters training unverified.
        """
        rows = self._conn.execute(
            "SELECT episode_id, instruction, plan_json, plan_risk, gate, status,"
            " expected_behavior, platform FROM episodes"
            " WHERE status IN ('completed','blocked','denied') ORDER BY episode_id"
        ).fetchall()

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ready = 0
        with out.open("w") as f:
            for eid, instruction, plan_json, risk, gate, status, label, plat in rows:
                is_ready = label is not None
                ready += is_ready
                f.write(json.dumps({
                    "episode_id": eid,
                    "instruction": instruction,
                    "plan": json.loads(plan_json),
                    "plan_risk": risk,
                    "gate": gate,
                    "outcome": status,
                    "expected_behavior": label,
                    "training_ready": is_ready,
                    "context": {"platform": plat},
                    "source": "episode",
                    "verified_by": None,
                }) + "\n")
        return {"exported": len(rows), "training_ready": ready,
                "needs_review": len(rows) - ready}

    def stats(self) -> dict[str, int]:
        return dict(self._conn.execute(
            "SELECT status, COUNT(*) FROM episodes GROUP BY status"
        ).fetchall())

    def close(self) -> None:
        self._conn.close()
