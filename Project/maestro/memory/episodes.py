"""Episodic memory — the substrate of the learning loop (docs/02 §8, L0).

Every interaction is recorded as (instruction, plan, outcome). This is not just
history: successful episodes ARE future fine-tuning pairs. The loop:

    use MAESTRO -> episodes accumulate -> export_dataset() -> HUMAN REVIEW
    -> LoRA fine-tune (docs/05) -> better planner -> use MAESTRO ...

That is how the system gets better at *your* work over time: not one big
training run, but a flywheel fed by real usage.

**The labelling rule, which v0.2 got wrong and this version fixes.** A refused
unsafe instruction is an episode too, and exporting it as a plain "instruction ->
plan" pair would teach the model to comply with the request it was supposed to
refuse. So `export_dataset` writes an `expected_behavior` field derived from what
actually happened (execute_auto / execute_with_consent / clarify / refuse), keeps
refusals and denials as *negative* examples with no plan target, and marks every
row `verified_by: null`. Nothing enters training until a human sets that field.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id   TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    instruction  TEXT NOT NULL,
    input_mode   TEXT DEFAULT 'text',
    intent       TEXT,
    intent_conf  REAL,
    slots_json   TEXT,
    plan_json    TEXT,
    plan_risk    TEXT,
    gate         TEXT,
    consent      TEXT,
    status       TEXT NOT NULL,
    outcome      TEXT,
    steps_ok     INTEGER DEFAULT 0,
    steps_total  INTEGER DEFAULT 0,
    plan_ms      REAL DEFAULT 0,
    exec_ms      REAL DEFAULT 0,
    detail       TEXT,
    planner      TEXT,
    strategy     TEXT,
    platform     TEXT,
    critic_json  TEXT,
    variables_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_ep_status ON episodes(status);
CREATE INDEX IF NOT EXISTS idx_ep_intent ON episodes(intent);
"""

# status -> the behaviour a training example should teach (docs/05 §2).
BEHAVIOR_FROM_STATUS = {
    "completed": "execute",       # refined below using the gate
    "blocked": "refuse",
    "denied": "refuse",
    "clarified": "clarify",
    "failed": None,               # a failure teaches nothing; keep for analysis
    "rolled_back": None,
    "budget_exceeded": None,
    "previewed": None,        # nothing happened; nothing to learn
    "undone": None,           # reversed by the user; the plan is not an exemplar
    "undo_failed": None,      # the run completed but its reversal did not
}


@dataclass(frozen=True)
class Episode:
    episode_id: str
    ts: str
    instruction: str
    intent: str | None
    intent_conf: float | None
    plan_json: str | None
    plan_risk: str | None
    gate: str | None
    status: str
    steps_ok: int
    steps_total: int
    planner: str | None
    strategy: str | None


class EpisodeStore:
    def __init__(self, db_path: str | Path):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the web workspace (maestro/ui.py) serves
        # requests from handler threads and runs execution on a worker. Every
        # store access there is serialised by one lock, so this is safe; the CLI
        # is single-threaded and unaffected.
        self._conn = sqlite3.connect(str(p), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns that older episode databases lack.

        `variables_json` holds the bound `$var` values at the end of a run —
        the move manifests in particular. Without it the undo machinery only
        works inside the process that ran the plan, which is useless for a
        user who closed the terminal and came back to reverse something.
        """
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(episodes)")}
        if "variables_json" not in cols:
            self._conn.execute("ALTER TABLE episodes ADD COLUMN variables_json TEXT")

    # -- writing -----------------------------------------------------------

    def new_id(self) -> str:
        return f"e_{uuid.uuid4().hex[:10]}"

    def record(
        self,
        *,
        instruction: str,
        status: str,
        episode_id: str | None = None,
        input_mode: str = "text",
        intent: str | None = None,
        intent_conf: float | None = None,
        slots: dict | None = None,
        plan_json: str | None = None,
        plan_risk: str | None = None,
        gate: str | None = None,
        consent: str | None = None,
        steps_ok: int = 0,
        steps_total: int = 0,
        plan_ms: float = 0.0,
        exec_ms: float = 0.0,
        detail: str = "",
        planner: str = "",
        strategy: str = "",
        platform: str = "",
        critic: list[dict] | None = None,
        variables: dict | None = None,
    ) -> str:
        eid = episode_id or self.new_id()
        self._conn.execute(
            "INSERT OR REPLACE INTO episodes (episode_id, ts, instruction, input_mode,"
            " intent, intent_conf, slots_json, plan_json, plan_risk, gate, consent,"
            " status, outcome, steps_ok, steps_total, plan_ms, exec_ms, detail, planner,"
            " strategy, platform, critic_json, variables_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                eid, datetime.now(timezone.utc).isoformat(), instruction, input_mode,
                intent, intent_conf,
                json.dumps(slots) if slots else None,
                plan_json, plan_risk, gate, consent, status,
                _outcome(status), steps_ok, steps_total, plan_ms, exec_ms, detail,
                planner, strategy, platform,
                json.dumps(critic) if critic else None,
                json.dumps(variables, default=str) if variables else None,
            ),
        )
        self._conn.commit()
        return eid

    # -- reading -----------------------------------------------------------

    def get(self, episode_id: str) -> dict | None:
        cur = self._conn.execute("SELECT * FROM episodes WHERE episode_id = ?",
                                 (episode_id,))
        row = cur.fetchone()
        if not row:
            return None
        return dict(zip([c[0] for c in cur.description], row))

    def recent(self, limit: int = 20) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM episodes ORDER BY ts DESC LIMIT ?", (limit,)
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def last_undoable(self, episode_id: str | None = None) -> dict | None:
        """The most recent completed run that still has something to reverse.

        Returns the episode row (with `plan_json` and `variables_json`) or None.
        A run is undoable when it completed, its plan declares at least one
        `undo`, and its variables were persisted. `maestro undo` builds on this.
        """
        # Eligibility is the same whether the caller names an episode or asks
        # for the latest: the run must have COMPLETED (so every planned action
        # executed, which is what the undo callers assume), its bound variables
        # must be persisted (the move manifests live there), and the plan must
        # declare at least one inverse. Previews, denials, failures and runs
        # already undone are never eligible; a run whose undo FAILED is, so it
        # can be retried.
        if episode_id:
            row = self.get(episode_id)
            return row if row and self.is_undoable(row) else None
        cur = self._conn.execute(
            "SELECT * FROM episodes WHERE status IN ('completed', 'undo_failed')"
            " AND plan_json IS NOT NULL AND variables_json IS NOT NULL"
            " ORDER BY ts DESC LIMIT 25"
        )
        cols = [c[0] for c in cur.description]
        for raw in cur.fetchall():
            row = dict(zip(cols, raw))
            if self.is_undoable(row):
                return row
        return None

    UNDOABLE_STATUSES = ("completed", "undo_failed")

    @staticmethod
    def is_undoable(row: dict) -> bool:
        if row.get("status") not in EpisodeStore.UNDOABLE_STATUSES:
            return False
        if not row.get("plan_json") or row.get("variables_json") is None:
            return False
        try:
            plan = json.loads(row["plan_json"])
        except json.JSONDecodeError:
            return False
        return any(a.get("undo") for a in plan.get("actions", []))

    def mark_undone(self, episode_id: str, detail: str) -> None:
        """Record that a run was reversed AND verified, so `last_undoable` skips
        it next time and the history shows what happened. Only for a successful
        undo — see `mark_undo_failed`."""
        self._conn.execute(
            "UPDATE episodes SET status = 'undone', outcome = 'undone', detail = ?"
            " WHERE episode_id = ?", (detail[:500], episode_id))
        self._conn.commit()

    def mark_undo_failed(self, episode_id: str, detail: str) -> None:
        """Record a failed or partial undo as exactly that. The row stays
        eligible for `last_undoable`, so a retry is one command away, and the
        history never shows a failed reversal as a clean one."""
        self._conn.execute(
            "UPDATE episodes SET status = 'undo_failed', outcome = 'undo_failed', detail = ?"
            " WHERE episode_id = ?", (detail[:500], episode_id))
        self._conn.commit()

    def stats(self) -> dict[str, int]:
        return dict(self._conn.execute(
            "SELECT status, COUNT(*) FROM episodes GROUP BY status"
        ).fetchall())

    def intent_counts(self) -> dict[str, int]:
        return dict(self._conn.execute(
            "SELECT COALESCE(intent,'?'), COUNT(*) FROM episodes GROUP BY intent"
        ).fetchall())

    def successful_exemplars(self, intent: str | None = None, limit: int = 5
                             ) -> list[tuple[str, dict]]:
        """Few-shot exemplars for the planner, drawn from this user's own history.

        This is what "learns your patterns" means concretely before any
        fine-tune has happened: the planner's exemplars stop being the three
        generic ones in prompts.py and become plans you already approved.
        """
        sql = ("SELECT instruction, plan_json FROM episodes WHERE status='completed'"
               " AND plan_json IS NOT NULL")
        params: tuple = ()
        if intent:
            sql += " AND intent = ?"
            params = (intent,)
        sql += " ORDER BY ts DESC LIMIT ?"
        params = (*params, limit)
        out = []
        for instruction, plan_json in self._conn.execute(sql, params):
            try:
                plan = json.loads(plan_json)
            except json.JSONDecodeError:
                continue
            out.append((instruction, {"actions": [
                {k: v for k, v in a.items()
                 if k in ("action_id", "verb", "args", "depends_on", "produces",
                          "rationale")}
                for a in plan.get("actions", [])
            ]}))
        return out

    # -- the learning loop -------------------------------------------------

    def export_dataset(self, out_path: str | Path, *, include_negatives: bool = True
                       ) -> dict[str, int]:
        """Write episodes as JSONL training CANDIDATES (docs/05 §2).

        Returns a per-behaviour count. Every row carries `verified_by: null`;
        the dataset rule is absolute — nothing enters training unverified by a
        human, and a refused instruction is exported as a refusal, never as a
        plan to imitate.
        """
        cur = self._conn.execute(
            "SELECT instruction, intent, plan_json, plan_risk, gate, status, detail,"
            " planner, strategy FROM episodes ORDER BY ts"
        )
        counts: dict[str, int] = {}
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for (instruction, intent, plan_json, plan_risk, gate, status, detail,
                 planner, strategy) in cur:
                behavior = expected_behavior(status, gate)
                if behavior is None:
                    continue
                if behavior == "refuse" and not include_negatives:
                    continue
                record = {
                    "instruction": instruction,
                    "intent": intent,
                    "expected_behavior": behavior,
                    # A refusal has NO plan target. Exporting the attempted plan
                    # here is exactly the v0.2 bug this field exists to fix.
                    "plan": json.loads(plan_json) if (plan_json and
                                                      behavior.startswith("execute"))
                    else None,
                    "plan_risk": plan_risk,
                    "gate": gate,
                    "refusal_reason": detail if behavior == "refuse" else None,
                    "source": "episode",
                    "planner": planner,
                    "strategy": strategy,
                    "verified_by": None,  # HUMAN REVIEW REQUIRED — not optional
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                counts[behavior] = counts.get(behavior, 0) + 1
        return counts

    def close(self) -> None:
        self._conn.close()


def expected_behavior(status: str, gate: str | None) -> str | None:
    """Map an outcome onto the dataset's `expected_behavior` label."""
    base = BEHAVIOR_FROM_STATUS.get(status)
    if base is None:
        return None
    if base == "execute":
        return "execute_auto" if gate == "auto" else "execute_with_consent"
    return base


def _outcome(status: str) -> str:
    return {
        "completed": "success",
        "blocked": "blocked",
        "denied": "aborted",
        "clarified": "clarified",
        "failed": "failed",
        "rolled_back": "failed",
        "budget_exceeded": "aborted",
        "previewed": "previewed",
        "undone": "undone",
        "undo_failed": "undo_failed",
    }.get(status, status)
