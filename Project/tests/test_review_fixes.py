"""Three findings from the presentation review, each pinned by a test.

1. Approval was checked only inside the consent callback, which R0/R1 plans
   never consult — so a changed LOW-risk plan could execute after a preview
   of a different one. Now the pipeline compares fingerprints before the
   orchestrator runs, for every tier.
2. `last_undoable(episode_id)` accepted any row with a plan, previews and
   already-undone runs included; the callers then assumed every action had
   executed. Now eligibility is identical for named and latest lookups.
3. A failed or partial undo was filed as "undone". Now it is recorded as
   `undo_failed`, stays visible as such, and remains eligible for a retry.
"""

from __future__ import annotations

import json

import maestro.executor  # noqa: F401
from maestro.ir import Action, Plan, UndoSpec
from maestro.memory import EpisodeStore
from maestro.nlp import entities as ents
from maestro.orchestrator import UndoReport
from maestro.pipeline import MaestroPipeline, auto_approve
from maestro.safety import AuditLog, ConsentGate
from maestro.ui import Workspace

# --------------------------------------------------------------------------- #
# 1. fingerprint binding for every tier
# --------------------------------------------------------------------------- #


def _alias(workspace, **names):
    saved = dict(ents.FOLDER_ALIASES)
    for k, rel in names.items():
        ents.FOLDER_ALIASES[k] = str(workspace / rel).replace("\\", "/")
    return saved


def test_pipeline_denies_a_changed_plan_before_execution_even_at_r1(policy, workspace,
                                                                    monkeypatch):
    """R1 (copy) plans auto-approve and never reach the consent callback, so
    the check has to live before the orchestrator — this is the tier the
    review found unprotected."""
    saved = _alias(workspace, inbox="inbox", archive="archive")
    (workspace / "inbox").mkdir(parents=True)
    (workspace / "inbox" / "a.pdf").write_text("x")
    try:
        pipe = MaestroPipeline(policy=policy, gate=ConsentGate(ask=auto_approve))
        preview = pipe.handle("back up the pdfs in inbox to archive", preview_only=True)
        assert preview.status == "previewed" and preview.risk == "R1"
        assert preview.gate == "auto"                       # the callback is never asked

        real = pipe.planner.plan

        def elsewhere(instruction, intent=None, slots=None):
            plan = real(instruction, intent, slots)
            for a in plan.actions:
                if "dest_dir" in a.args:
                    a.args["dest_dir"] = str(workspace / "somewhere_else")
            return plan

        monkeypatch.setattr(pipe.planner, "plan", elsewhere)
        turn = pipe.handle("back up the pdfs in inbox to archive",
                           expected_fingerprint=preview.plan.fingerprint())
        assert turn.status == "denied"
        assert "changed between preview and approval" in turn.message
        assert not (workspace / "somewhere_else").exists()   # nothing ran
        assert not (workspace / "archive").exists()
        assert pipe.episodes.stats().get("denied") == 1
        pipe.close()
    finally:
        ents.FOLDER_ALIASES.clear()
        ents.FOLDER_ALIASES.update(saved)


def test_pipeline_accepts_the_same_plan(policy, workspace):
    saved = _alias(workspace, inbox="inbox", archive="archive")
    (workspace / "inbox").mkdir(parents=True)
    (workspace / "inbox" / "a.pdf").write_text("x")
    try:
        pipe = MaestroPipeline(policy=policy, gate=ConsentGate(ask=auto_approve))
        preview = pipe.handle("back up the pdfs in inbox to archive", preview_only=True)
        turn = pipe.handle("back up the pdfs in inbox to archive",
                           expected_fingerprint=preview.plan.fingerprint())
        assert turn.status == "completed"
        assert (workspace / "archive" / "a.pdf").exists()
        pipe.close()
    finally:
        ents.FOLDER_ALIASES.clear()
        ents.FOLDER_ALIASES.update(saved)


def test_workspace_binds_low_risk_approval(policy, workspace, tmp_path, monkeypatch):
    """The same property through the UI: an R1 preview, a changed replan,
    approval — must not execute."""
    saved = _alias(workspace, inbox="inbox", archive="archive")
    (workspace / "inbox").mkdir(parents=True)
    (workspace / "inbox" / "a.pdf").write_text("x")
    try:
        ws = Workspace(MaestroPipeline(policy=policy, audit=AuditLog(tmp_path / "a.db")))
        out = ws.plan("back up the pdfs in inbox to archive")
        assert out["status"] == "previewed" and out["gate"] == "auto"
        real = ws.pipe.planner.plan

        def elsewhere(instruction, intent=None, slots=None):
            plan = real(instruction, intent, slots)
            for a in plan.actions:
                if "dest_dir" in a.args:
                    a.args["dest_dir"] = str(workspace / "somewhere_else")
            return plan

        monkeypatch.setattr(ws.pipe.planner, "plan", elsewhere)
        ws.approve()
        import time
        for _ in range(200):
            p = ws.progress_json()
            if p["done"]:
                break
            time.sleep(0.05)
        assert p["result"]["status"] == "denied"
        assert not (workspace / "somewhere_else").exists()
        ws.close()
    finally:
        ents.FOLDER_ALIASES.clear()
        ents.FOLDER_ALIASES.update(saved)


# --------------------------------------------------------------------------- #
# 2. undo eligibility
# --------------------------------------------------------------------------- #


def _plan_with_undo() -> str:
    return Plan(plan_id="p", instruction="move", actions=[
        Action(action_id="a1", verb="fs.move_batch",
               args={"sources": ["/x/a"], "dest_dir": "/y"}, produces="moved",
               undo=UndoSpec(verb="fs.restore_manifest", args={"manifest": "$moved"})),
    ]).model_dump_json()


def test_named_episode_must_be_completed_and_reversible(tmp_path):
    store = EpisodeStore(tmp_path / "e.db")
    pj = _plan_with_undo()
    previewed = store.record(instruction="move", status="previewed", plan_json=pj)
    completed_no_vars = store.record(instruction="move", status="completed", plan_json=pj)
    completed = store.record(instruction="move", status="completed", plan_json=pj,
                             variables={"moved": []})
    no_inverse = store.record(instruction="glob", status="completed",
                              plan_json=Plan(plan_id="q", instruction="g", actions=[
                                  Action(action_id="a1", verb="fs.glob",
                                         args={"root": "/x", "pattern": "*"})]).model_dump_json(),
                              variables={})

    assert store.last_undoable(previewed) is None            # a preview never ran
    assert store.last_undoable(completed_no_vars) is None    # no persisted manifests
    assert store.last_undoable(no_inverse) is None           # nothing to reverse
    assert store.last_undoable(completed)["episode_id"] == completed

    store.mark_undone(completed, "ok")
    assert store.last_undoable(completed) is None            # already reversed
    assert store.last_undoable() is None
    store.close()


# --------------------------------------------------------------------------- #
# 3. failed undo is recorded as failed
# --------------------------------------------------------------------------- #


def test_failed_undo_is_recorded_separately_and_stays_retryable(policy, workspace,
                                                                tmp_path, monkeypatch):
    saved = _alias(workspace, inbox="inbox", archive="archive")
    (workspace / "inbox").mkdir(parents=True)
    (workspace / "inbox" / "a.pdf").write_text("x")
    try:
        ws = Workspace(MaestroPipeline(policy=policy, audit=AuditLog(tmp_path / "a.db")))
        ws.plan("move the pdfs from inbox to archive")
        ws.approve()
        import time
        for _ in range(200):
            if ws.progress_json()["done"]:
                break
            time.sleep(0.05)
        row = next(r for r in ws.history() if r["status"] == "completed")

        # Make the reversal fail: the moved file is gone before undo runs.
        (workspace / "archive" / "a.pdf").unlink()
        out = ws.undo(row["episode_id"])
        assert out["ok"] is False
        after = next(r for r in ws.history() if r["episode_id"] == row["episode_id"])
        assert after["status"] == "undo_failed"              # not "undone"
        assert after["undoable"] is True                      # retry is offered
        assert ws.pipe.episodes.last_undoable()["episode_id"] == row["episode_id"]
        ws.close()
    finally:
        ents.FOLDER_ALIASES.clear()
        ents.FOLDER_ALIASES.update(saved)


def test_undo_report_ok_is_false_on_partial(tmp_path):
    r = UndoReport(plan_id="p", reversed=1, verified_files=1, mismatched_files=1)
    assert r.ok is False
    r2 = UndoReport(plan_id="p", reversed=1, verified_files=2)
    assert r2.ok is True
    assert json.dumps(r.render()) # renders without error
