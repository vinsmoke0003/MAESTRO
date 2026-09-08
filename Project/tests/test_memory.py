"""Memory: episodes, the learning-loop export, preferences, exemplar retrieval.

The export tests are the important ones. v0.2 of this project shipped a bug
where a refused unsafe instruction was exported as a "completed" episode — i.e.
as a training pair teaching the model to comply with the request it had just
correctly refused. These tests exist so that cannot come back.
"""

from __future__ import annotations

import json

import maestro.executor  # noqa: F401
from maestro.memory import EpisodeStore, PreferenceStore, expected_behavior, open_store
from maestro.memory.preferences import MIN_OBSERVATIONS

# =========================================================================== #
# episodes
# =========================================================================== #


def test_record_and_read_back(tmp_path):
    store = EpisodeStore(tmp_path / "ep.db")
    eid = store.record(instruction="move the pdfs", status="completed",
                       intent="FILE_ORGANIZE", intent_conf=0.94, plan_risk="R2",
                       gate="confirm", steps_ok=3, steps_total=3)
    row = store.get(eid)
    assert row["instruction"] == "move the pdfs"
    assert row["outcome"] == "success"
    assert store.stats() == {"completed": 1}


def test_stats_and_intent_counts(tmp_path):
    store = EpisodeStore(tmp_path / "ep.db")
    store.record(instruction="a", status="completed", intent="FILE_ORGANIZE")
    store.record(instruction="b", status="blocked", intent="UNSAFE_REQUEST")
    store.record(instruction="c", status="clarified", intent="FILE_DELETE")
    assert store.stats() == {"completed": 1, "blocked": 1, "clarified": 1}
    assert store.intent_counts()["UNSAFE_REQUEST"] == 1


# --- the labelling rule ---------------------------------------------------- #


def test_expected_behavior_mapping():
    assert expected_behavior("completed", "auto") == "execute_auto"
    assert expected_behavior("completed", "confirm") == "execute_with_consent"
    assert expected_behavior("blocked", None) == "refuse"
    assert expected_behavior("denied", None) == "refuse"
    assert expected_behavior("clarified", None) == "clarify"
    # A failure teaches nothing; it stays in the table for analysis only.
    assert expected_behavior("failed", None) is None
    assert expected_behavior("rolled_back", None) is None


def test_a_refused_episode_exports_as_a_refusal_not_a_plan(tmp_path):
    """The v0.2 bug, pinned.

    A blocked instruction must never export with the plan that was attempted:
    that pair would train the model to do exactly what it correctly refused.
    """
    store = EpisodeStore(tmp_path / "ep.db")
    store.record(
        instruction="permanently delete everything in Documents",
        status="blocked",
        intent="UNSAFE_REQUEST",
        plan_json=json.dumps({"actions": [{"verb": "fs.delete_permanent"}]}),
        detail="hard-blocked by policy",
    )
    out = tmp_path / "candidates.jsonl"
    counts = store.export_dataset(out)

    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert counts == {"refuse": 1}
    assert len(rows) == 1
    assert rows[0]["expected_behavior"] == "refuse"
    assert rows[0]["plan"] is None          # <- the fix
    assert rows[0]["refusal_reason"]


def test_a_completed_episode_exports_with_its_plan(tmp_path):
    store = EpisodeStore(tmp_path / "ep.db")
    store.record(instruction="move the pdfs", status="completed", gate="confirm",
                 plan_json=json.dumps({"actions": [{"verb": "fs.glob"}]}))
    out = tmp_path / "c.jsonl"
    store.export_dataset(out)
    row = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert row["expected_behavior"] == "execute_with_consent"
    assert row["plan"] is not None


def test_every_exported_row_needs_human_verification(tmp_path):
    """docs/05 §2: nothing enters training unverified. The field is written as
    null so an unreviewed row cannot be mistaken for a reviewed one."""
    store = EpisodeStore(tmp_path / "ep.db")
    store.record(instruction="a", status="completed", gate="auto",
                 plan_json=json.dumps({"actions": []}))
    store.record(instruction="b", status="blocked")
    out = tmp_path / "c.jsonl"
    store.export_dataset(out)
    for line in out.read_text(encoding="utf-8").splitlines():
        assert json.loads(line)["verified_by"] is None


def test_failed_episodes_are_kept_but_not_exported(tmp_path):
    store = EpisodeStore(tmp_path / "ep.db")
    store.record(instruction="broken", status="failed")
    out = tmp_path / "c.jsonl"
    assert store.export_dataset(out) == {}
    assert store.stats() == {"failed": 1}   # still there for failure analysis


def test_exemplars_come_only_from_successful_episodes(tmp_path):
    store = EpisodeStore(tmp_path / "ep.db")
    plan = json.dumps({"actions": [{"action_id": "a1", "verb": "fs.glob",
                                    "args": {"root": "~/Downloads"}}]})
    store.record(instruction="good one", status="completed",
                 intent="FILE_ORGANIZE", plan_json=plan)
    store.record(instruction="refused one", status="blocked",
                 intent="FILE_ORGANIZE", plan_json=plan)
    exemplars = store.successful_exemplars("FILE_ORGANIZE")
    assert [i for i, _ in exemplars] == ["good one"]


# =========================================================================== #
# preferences
# =========================================================================== #


def test_a_preference_needs_repetition_before_it_counts(tmp_path):
    """One instruction is not a habit. A preference that activated on a single
    observation would let a one-off request rewrite the user's defaults."""
    prefs = PreferenceStore(tmp_path / "p.db")
    p = prefs.observe("invoices", "~/Documents/Finance")
    assert not p.active
    for _ in range(MIN_OBSERVATIONS):
        p = prefs.observe("invoices", "~/Documents/Finance")
    assert p.active
    assert prefs.known_paths()["invoices"] == "~/Documents/Finance"


def test_a_contradiction_does_not_immediately_flip_a_habit(tmp_path):
    prefs = PreferenceStore(tmp_path / "p.db")
    for _ in range(4):
        prefs.observe("invoices", "~/Documents/Finance")
    prefs.observe("invoices", "~/Desktop")
    assert prefs.get("invoices").value == "~/Documents/Finance"


def test_enough_contradictions_do_flip_it(tmp_path):
    prefs = PreferenceStore(tmp_path / "p.db")
    prefs.observe("invoices", "~/Documents/Finance")
    for _ in range(3):
        prefs.observe("invoices", "~/Documents/Taxes")
    assert prefs.get("invoices").value == "~/Documents/Taxes"


def test_the_user_can_inspect_edit_and_delete(tmp_path):
    """FR-53. This is why preferences are a table and not weights."""
    prefs = PreferenceStore(tmp_path / "p.db")
    prefs.set("thesis", "~/Documents/Thesis")
    assert prefs.get("thesis").active
    assert any(p.key == "thesis" for p in prefs.all())
    assert prefs.forget("thesis")
    assert prefs.get("thesis") is None


def test_learning_from_an_approved_plan(tmp_path):
    from maestro.ir import Action, Plan

    prefs = PreferenceStore(tmp_path / "p.db")
    plan = Plan(plan_id="p", instruction="move the invoices to the finance folder",
                actions=[Action(action_id="a1", verb="fs.move_batch",
                                args={"sources": ["~/Downloads/a.pdf"],
                                      "dest_dir": "~/Documents/Finance"})])
    prefs.learn_from_plan(plan.instruction, plan)
    prefs.learn_from_plan(plan.instruction, plan)
    assert prefs.known_paths().get("invoices") == "~/Documents/Finance"


# =========================================================================== #
# exemplar store
# =========================================================================== #


def test_hashing_store_retrieves_by_similarity(tmp_path):
    store = open_store(tmp_path / "vectors", prefer_chroma=False)
    store.add("p1", "move the pdfs from Downloads to Invoices",
              {"actions": [{"verb": "fs.move_batch"}]}, "FILE_ORGANIZE")
    store.add("p2", "how much disk space is left",
              {"actions": [{"verb": "sys.info"}]}, "SYSTEM_QUERY")
    hits = store.query("move my pdfs to invoices", k=1)
    assert hits and hits[0].doc_id == "p1"


def test_exemplar_store_survives_without_chromadb(tmp_path):
    store = open_store(tmp_path / "vectors", prefer_chroma=False)
    assert store.backend == "hashing"
    assert store.count() == 0


def test_exemplar_store_filters_by_intent(tmp_path):
    store = open_store(tmp_path / "v", prefer_chroma=False)
    store.add("p1", "move the pdfs", {"actions": []}, "FILE_ORGANIZE")
    store.add("p2", "move the pdfs", {"actions": []}, "FILE_DELETE")
    hits = store.query("move the pdfs", k=5, intent="FILE_DELETE")
    assert [h.doc_id for h in hits] == ["p2"]
