"""Preview is a MODE, not a consent callback that says no.

The bug this pins: `maestro plan` used a deny-everything consent callback to
stop execution. R0/R1 plans never consult that callback — by design, to avoid
prompt habituation — so an R1 "back up the pdfs" plan *executed* under a command
whose whole purpose was to not execute. The archive directory appeared, the copy
happened, and the user had been told they were looking at a preview.

Every test here asserts the same thing from a different tier: after
`preview_only=True`, the filesystem is byte-identical and no backend was called.
"""

from __future__ import annotations

import maestro.executor  # noqa: F401
from maestro.ir import Action, Plan, UndoSpec
from maestro.orchestrator import Orchestrator
from maestro.pipeline import MaestroPipeline, auto_approve
from maestro.safety import ConsentGate


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def orch(policy, **kw) -> Orchestrator:
    from maestro.safety.consent import Approval

    return Orchestrator(policy=policy, gate=ConsentGate(ask=lambda r: Approval(True, "typed")),
                        **kw)


# --------------------------------------------------------------------------- #
# R1 — the tier that was actually executing
# --------------------------------------------------------------------------- #


def test_preview_does_not_execute_an_r1_plan(policy, workspace, files):
    """The exact repro: glob -> mkdir -> copy_batch is R1, gate 'auto'."""
    files("inbox", "a.pdf", "b.pdf")
    before = snapshot(workspace)
    plan = Plan(plan_id="p", instruction="back up the pdfs", actions=[
        Action(action_id="a1", verb="fs.glob",
               args={"root": str(workspace / "inbox"), "pattern": "*.pdf"},
               produces="matched"),
        Action(action_id="a2", verb="fs.mkdir", args={"path": str(workspace / "archive")}),
        Action(action_id="a3", verb="fs.copy_batch",
               args={"sources": "$matched", "dest_dir": str(workspace / "archive")},
               depends_on=["a1", "a2"]),
    ])
    report = orch(policy).run(plan, preview_only=True)
    assert report.status == "previewed"
    assert report.gate == "auto"                    # it WOULD have auto-run
    assert not (workspace / "archive").exists()     # and it did not
    assert snapshot(workspace) == before
    assert report.steps == []                       # nothing was even attempted


def test_preview_still_produces_a_real_manifest(policy, workspace, files):
    """Preview must be informative, not just inert: the dry run still reports
    the measured file count, because that is what the user is inspecting."""
    files("inbox", "a.pdf", "b.pdf", "c.pdf")
    plan = Plan(plan_id="p", instruction="x", actions=[
        Action(action_id="a1", verb="fs.glob",
               args={"root": str(workspace / "inbox"), "pattern": "*.pdf"}),
    ])
    report = orch(policy).run(plan, preview_only=True)
    assert report.manifests[0].files_touched == 3


# --------------------------------------------------------------------------- #
# R1 with a side-effecting backend — app.launch
# --------------------------------------------------------------------------- #


def test_preview_does_not_launch_an_application(policy, monkeypatch):
    """`app.launch` is R1 and cannot be checked on the filesystem, so instrument
    the backend: it must not be called at all."""
    launched = []
    import maestro.executor.app as app_mod

    class _FakeBackend:
        @staticmethod
        def launch(app_id):
            launched.append(app_id)
            return "launched"

        @staticmethod
        def quit(app_id, force=False):
            return "quit"

    monkeypatch.setattr(app_mod, "backend", lambda name: _FakeBackend)
    plan = Plan(plan_id="p", instruction="open chrome", actions=[
        Action(action_id="a1", verb="app.launch", args={"app_id": "chrome"}),
    ])
    report = orch(policy).run(plan, preview_only=True)
    assert report.status == "previewed"
    assert launched == []


# --------------------------------------------------------------------------- #
# R2 / R3 — the tiers the old approach happened to stop
# --------------------------------------------------------------------------- #


def test_preview_does_not_execute_an_r2_plan_even_with_an_approving_gate(
        policy, workspace, files):
    """The gate is never consulted in preview, so an approving gate changes
    nothing — otherwise preview would depend on which callback happened to be
    wired in, which is the property that failed."""
    made = files("inbox", "a.pdf")
    plan = Plan(plan_id="p", instruction="move", actions=[
        Action(action_id="a1", verb="fs.move_batch",
               args={"sources": [str(made[0])], "dest_dir": str(workspace / "out")},
               produces="moved",
               undo=UndoSpec(verb="fs.restore_manifest", args={"manifest": "$moved"})),
    ])
    report = orch(policy).run(plan, preview_only=True)
    assert report.status == "previewed"
    assert report.gate == "confirm"
    assert report.approval is None          # the gate was never asked
    assert made[0].exists()


def test_preview_of_an_r3_plan_names_the_typed_gate(policy):
    plan = Plan(plan_id="p", instruction="click", actions=[
        Action(action_id="a1", verb="browser.click",
               args={"url": "https://example.com", "selector": "#buy"}),
    ])
    report = orch(policy).run(plan, preview_only=True)
    assert report.status == "previewed"
    assert "typed_confirm" in report.message


def test_preview_of_a_blocked_plan_is_still_blocked(policy, workspace):
    """Preview must not soften a hard block into a 'would need consent'."""
    plan = Plan(plan_id="p", instruction="nuke", actions=[
        Action(action_id="a1", verb="fs.delete_permanent",
               args={"paths": [str(workspace / "x")]}),
    ])
    report = orch(policy).run(plan, preview_only=True)
    assert report.status == "blocked"


# --------------------------------------------------------------------------- #
# audit and memory
# --------------------------------------------------------------------------- #


def test_preview_is_audited_as_previewed_not_executed(policy, workspace, files, audit):
    files("inbox", "a.pdf")
    plan = Plan(plan_id="p", instruction="x", actions=[
        Action(action_id="a1", verb="fs.mkdir", args={"path": str(workspace / "d")}),
    ])
    orch(policy, audit=audit).run(plan, preview_only=True)
    events = [r.event for r in audit.rows()]
    assert "PREVIEWED" in events
    assert "EXECUTED" not in events
    assert audit.verify()


def test_pipeline_preview_learns_nothing(policy, workspace, monkeypatch):
    """A preview is not an approved plan, so it must not feed preferences or
    the exemplar store — otherwise previewing a bad plan would teach it."""
    from maestro.nlp import entities as ents

    saved = dict(ents.FOLDER_ALIASES)
    ents.FOLDER_ALIASES["inbox"] = str(workspace / "inbox").replace("\\", "/")
    ents.FOLDER_ALIASES["archive"] = str(workspace / "archive").replace("\\", "/")
    (workspace / "inbox").mkdir(parents=True, exist_ok=True)
    (workspace / "inbox" / "a.pdf").write_text("x")
    try:
        p = MaestroPipeline(policy=policy, gate=ConsentGate(ask=auto_approve))
        turn = p.handle("move the pdfs from inbox to archive", preview_only=True)
        assert turn.status == "previewed"
        assert turn.preview and "gate: confirm" in turn.preview
        assert (workspace / "inbox" / "a.pdf").exists()
        assert p.vectors.count() == 0
        assert p.episodes.stats() == {"previewed": 1}
        p.close()
    finally:
        ents.FOLDER_ALIASES.clear()
        ents.FOLDER_ALIASES.update(saved)


def test_previewed_episodes_are_not_training_candidates():
    from maestro.memory import expected_behavior

    assert expected_behavior("previewed", "confirm") is None
