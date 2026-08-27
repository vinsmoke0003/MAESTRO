"""L0 store tests: migration, preferences, durable undo, and the labeling gate."""

import json
import sqlite3

import pytest

from maestro.memory import EpisodeStore, PreferenceStore, UndoStack, schema

PLAN = json.dumps({"plan_id": "p1", "actions": []})


def _episode(store, instruction="move the pdfs", status="completed", risk="R2"):
    return store.record(instruction=instruction, plan_json=PLAN, plan_risk=risk,
                        gate="confirm", status=status, planner="qwen2.5:7b")


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------

def test_migration_preserves_existing_v02_episodes(tmp_path):
    """A v0.2 database keeps its history and gains the new columns."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE episodes (
            episode_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            instruction TEXT NOT NULL, plan_json TEXT NOT NULL, plan_risk TEXT,
            gate TEXT, status TEXT NOT NULL, detail TEXT, planner TEXT);
    """)
    conn.execute("INSERT INTO episodes (ts, instruction, plan_json, status)"
                 " VALUES ('2026-01-01', 'old instruction', ?, 'completed')", (PLAN,))
    conn.commit()
    conn.close()

    conn = schema.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
    assert {"expected_behavior", "plan_ms", "consent", "platform"} <= cols
    row = conn.execute("SELECT instruction FROM episodes").fetchone()
    assert row[0] == "old instruction"  # history survived
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "m.db"
    conn = schema.connect(db)
    assert schema.migrate(conn) == []      # second pass adds nothing
    conn.close()


# --------------------------------------------------------------------------
# the labeling gate — refusals must teach refusal (docs/05 §2)
# --------------------------------------------------------------------------

def test_refused_episodes_are_exported_and_not_training_ready(tmp_path):
    """The documented v0.2 issue: a blocked plan must not look like compliance."""
    store = EpisodeStore(tmp_path / "e.db")
    _episode(store, "delete everything permanently", status="blocked", risk="BLOCKED")
    _episode(store, "move the pdfs", status="completed")

    out = tmp_path / "cand.jsonl"
    report = store.export_dataset(out)
    rows = [json.loads(line) for line in out.read_text().splitlines()]

    assert report["exported"] == 2
    # the refusal IS exported (it is the only thing that teaches refusal)...
    blocked = next(r for r in rows if r["outcome"] == "blocked")
    assert blocked["instruction"] == "delete everything permanently"
    # ...but nothing is training-ready until a human has labeled it.
    assert report["training_ready"] == 0
    assert all(r["training_ready"] is False for r in rows)
    assert all(r["expected_behavior"] is None for r in rows)


def test_labeling_makes_an_episode_training_ready(tmp_path):
    store = EpisodeStore(tmp_path / "e.db")
    eid = _episode(store, "delete everything", status="blocked")
    store.label(eid, "refuse")

    report = store.export_dataset(tmp_path / "c.jsonl")
    assert report["training_ready"] == 1
    assert report["needs_review"] == 0


def test_invalid_label_is_rejected(tmp_path):
    store = EpisodeStore(tmp_path / "e.db")
    eid = _episode(store)
    with pytest.raises(ValueError):
        store.label(eid, "probably_fine")


def test_review_queue_puts_refusals_first(tmp_path):
    store = EpisodeStore(tmp_path / "e.db")
    _episode(store, "ok task", status="completed")
    _episode(store, "unsafe task", status="blocked")
    queue = store.unlabeled()
    assert queue[0]["instruction"] == "unsafe task"
    assert queue[0]["suggested"] == "refuse"


# --------------------------------------------------------------------------
# preferences and durable undo
# --------------------------------------------------------------------------

def test_preferences_round_trip_and_forget(tmp_path):
    conn = schema.connect(tmp_path / "p.db")
    prefs = PreferenceStore(conn)
    prefs.set("invoices_dir", "~/Documents/Finance", confidence=0.9,
              learned_from="episode:12")
    assert prefs.get("invoices_dir") == "~/Documents/Finance"
    # a low-confidence preference is withheld when the caller demands certainty
    assert prefs.get("invoices_dir", min_confidence=0.95) is None
    assert prefs.forget("invoices_dir") is True      # FR-53
    assert prefs.get("invoices_dir") is None


def test_undo_survives_a_new_connection(tmp_path):
    """FR-29: 'undo that' must still work in the next session."""
    db = tmp_path / "u.db"
    conn = schema.connect(db)
    UndoStack(conn).push(episode_id=None, action_id="a2", verb="fs.move_batch",
                         undo_data={"manifest": ["/tmp/x"]})
    conn.close()

    conn2 = schema.connect(db)          # process restarted
    pending = UndoStack(conn2).pending()
    assert len(pending) == 1
    assert pending[0]["undo_data"] == {"manifest": ["/tmp/x"]}

    UndoStack(conn2).mark_applied(pending[0]["id"])
    assert UndoStack(conn2).pending() == []
