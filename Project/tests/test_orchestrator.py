"""The orchestrator: dry-run, gate, execute, verify, roll back.

The interesting tests here are the ones about *failure*: a postcondition that
does not hold, a step that dies halfway, a budget breach. Success paths are easy
to get right; the behaviour under partial failure is what the safety claims
actually depend on.
"""

from __future__ import annotations

import pytest

import maestro.executor  # noqa: F401
from maestro.executor.base import Result, get_executor
from maestro.ir import Action, Check, Plan, Risk, UndoSpec
from maestro.orchestrator import Orchestrator, render_preview
from maestro.safety import ConsentGate
from maestro.safety.consent import Approval


def plan_of(*actions, instruction="test") -> Plan:
    return Plan(plan_id="p_t", instruction=instruction, actions=list(actions))


def move_plan(workspace, sources, dest, with_postcondition=True) -> Plan:
    post = [Check(check="all_moved", args={"manifest": "$moved"})] \
        if with_postcondition else []
    return plan_of(
        Action(action_id="a1", verb="fs.move_batch",
               args={"sources": [str(s) for s in sources], "dest_dir": str(dest)},
               produces="moved", postconditions=post,
               undo=UndoSpec(verb="fs.restore_manifest",
                             args={"manifest": "$moved"})),
        instruction="move the files",
    )


def orch(policy, audit=None, gate=None, **kw) -> Orchestrator:
    return Orchestrator(policy=policy, audit=audit,
                        gate=gate or ConsentGate(ask=lambda r: Approval(True, "click")),
                        **kw)


# =========================================================================== #
# the happy path, and what it guarantees
# =========================================================================== #


def test_a_gated_plan_runs_after_approval(policy, workspace, files):
    made = files("inbox", "a.pdf", "b.pdf")
    dest = workspace / "archive"
    report = orch(policy).run(move_plan(workspace, made, dest))
    assert report.status == "completed"
    assert report.gate == "confirm"
    assert (dest / "a.pdf").exists() and not made[0].exists()


def test_nothing_touches_the_disk_before_consent(policy, workspace, files):
    """docs/02 §4 step 8->9: the dry run is what makes consent meaningful."""
    made = files("inbox", "a.pdf")
    seen = {}

    def inspect(req):
        seen["files_still_there"] = made[0].exists()
        seen["manifest_count"] = req.manifests[0].files_touched
        return Approval(True, "click")

    orch(policy, gate=ConsentGate(ask=inspect)).run(
        move_plan(workspace, made, workspace / "out"))
    assert seen["files_still_there"] is True
    assert seen["manifest_count"] == 1     # a measured number, not a guess


def test_denial_leaves_everything_untouched(policy, workspace, files):
    made = files("inbox", "a.pdf")
    gate = ConsentGate(ask=lambda r: Approval(False, "denied", note="no thanks"))
    report = orch(policy, gate=gate).run(move_plan(workspace, made, workspace / "out"))
    assert report.status == "denied"
    assert made[0].exists()
    assert not (workspace / "out").exists()


def test_low_risk_plans_never_prompt(policy, workspace, files):
    files("inbox", "a.pdf")
    asked = []
    gate = ConsentGate(ask=lambda r: asked.append(r) or Approval(True))
    plan = plan_of(Action(action_id="a1", verb="fs.glob",
                          args={"root": str(workspace / "inbox")}, produces="found"))
    report = orch(policy, gate=gate).run(plan)
    assert report.status == "completed" and asked == []


def test_blocked_plan_never_reaches_the_gate(policy, workspace):
    asked = []
    gate = ConsentGate(ask=lambda r: asked.append(r) or Approval(True))
    plan = plan_of(Action(action_id="a1", verb="fs.delete_permanent",
                          args={"paths": [str(workspace / "a")]}))
    report = orch(policy, gate=gate).run(plan)
    assert report.status == "blocked" and asked == []


# =========================================================================== #
# failure and rollback
# =========================================================================== #


def test_a_failed_step_rolls_back_the_completed_ones(policy, workspace, files):
    made = files("inbox", "a.pdf")
    dest = workspace / "archive"
    plan = plan_of(
        Action(action_id="a1", verb="fs.move_batch",
               args={"sources": [str(made[0])], "dest_dir": str(dest)},
               produces="moved",
               undo=UndoSpec(verb="fs.restore_manifest", args={"manifest": "$moved"})),
        Action(action_id="a2", verb="fs.copy",
               args={"src": str(workspace / "ghost.txt"), "dst": str(dest / "x.txt")},
               depends_on=["a1"]),
        instruction="move then fail",
    )
    report = orch(policy).run(plan)
    assert report.status == "rolled_back"
    assert made[0].exists()                 # step 1 was reversed
    assert not (dest / "a.pdf").exists()


def test_a_failed_postcondition_fails_the_step(policy, workspace, files, monkeypatch):
    """Threat T8. `ok=True` from an executor is not success — the Verifier
    decides, and a lying executor gets caught."""
    made = files("inbox", "a.pdf")
    dest = workspace / "archive"

    real = get_executor("fs.move_batch").execute

    lie = [{"from": str(made[0]), "to": str(dest / "never_written.pdf")}]

    def lying_execute(args, ctx):
        real(args, ctx)                      # actually move
        # ...then claim a file arrived that never did.
        return Result(ok=True, output=lie, detail="moved 1 file(s)", undo_data=lie)

    monkeypatch.setattr(get_executor("fs.move_batch"), "execute", lying_execute)
    report = orch(policy).run(move_plan(workspace, made, dest))
    assert report.status == "rolled_back"
    assert "postcondition failed" in report.message


def test_postconditions_can_be_ablated(policy, workspace, files, monkeypatch):
    """Ablation A3. With verification off, the same lie is reported as success —
    which is the number docs/07 §5 wants measured."""
    made = files("inbox", "a.pdf")
    dest = workspace / "archive"
    real = get_executor("fs.move_batch").execute
    lie = [{"from": str(made[0]), "to": str(dest / "never_written.pdf")}]

    def lying_execute(args, ctx):
        real(args, ctx)
        return Result(ok=True, output=lie, detail="moved", undo_data=lie)

    monkeypatch.setattr(get_executor("fs.move_batch"), "execute", lying_execute)
    report = orch(policy, enable_postconditions=False).run(
        move_plan(workspace, made, dest))
    assert report.status == "completed"     # the silent failure A3 exposes


def test_a_failed_precondition_stops_before_executing(policy, workspace):
    plan = plan_of(Action(
        action_id="a1", verb="fs.mkdir", args={"path": str(workspace / "new")},
        preconditions=[Check(check="path_exists",
                             args={"path": str(workspace / "missing")})]))
    report = orch(policy).run(plan)
    assert report.status == "rolled_back"
    assert "precondition failed" in report.message
    assert not (workspace / "new").exists()


def test_an_executor_exception_becomes_a_step_failure(policy, workspace, monkeypatch):
    def boom(args, ctx):
        raise RuntimeError("executor bug")

    monkeypatch.setattr(get_executor("fs.mkdir"), "execute", boom)
    report = orch(policy).run(plan_of(Action(
        action_id="a1", verb="fs.mkdir", args={"path": str(workspace / "d")})))
    assert report.status == "rolled_back"
    assert "RuntimeError" in report.message


def test_budget_breach_halts_and_rolls_back(policy, workspace, files):
    from maestro.ir import Budget

    made = files("inbox", *[f"f{i}.pdf" for i in range(6)])
    plan = Plan(plan_id="p", instruction="move lots",
                budget=Budget(max_steps=10, max_files_touched=2),
                actions=[Action(
                    action_id="a1", verb="fs.move_batch",
                    args={"sources": [str(p) for p in made],
                          "dest_dir": str(workspace / "out")},
                    produces="moved",
                    undo=UndoSpec(verb="fs.restore_manifest",
                                  args={"manifest": "$moved"}))])
    report = orch(policy).run(plan)
    assert report.status == "budget_exceeded"
    assert all(p.exists() for p in made)   # caught statically, before executing


# =========================================================================== #
# the frozen DAG
# =========================================================================== #


def test_the_plan_cannot_grow_during_execution(policy, workspace, files):
    """docs/02 §6 rule 4 — the hole through which a malicious document
    escalates "summarize" into "exfiltrate".

    The orchestrator iterates `plan.topo_order`, captured before execution.
    Appending to the plan mid-run has no effect on what runs.
    """
    files("inbox", "a.pdf")
    plan = plan_of(Action(action_id="a1", verb="fs.glob",
                          args={"root": str(workspace / "inbox")}, produces="found"))
    executed: list[str] = []
    real = get_executor("fs.glob").execute

    def watching(args, ctx):
        executed.append("a1")
        plan.actions.append(Action(action_id="a99", verb="fs.trash",
                                   args={"paths": [str(workspace / "inbox")]}))
        return real(args, ctx)

    orch(policy).run(plan)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(get_executor("fs.glob"), "execute", watching)
        report = orch(policy).run(plan_of(Action(
            action_id="a1", verb="fs.glob",
            args={"root": str(workspace / "inbox")}, produces="found")))
    assert [s.verb for s in report.steps] == ["fs.glob"]
    assert (workspace / "inbox").exists()


# =========================================================================== #
# undo
# =========================================================================== #


def test_session_undo_reverses_a_completed_run(policy, workspace, files):
    made = files("inbox", "a.pdf", "b.pdf")
    dest = workspace / "archive"
    o = orch(policy)
    plan = move_plan(workspace, made, dest)
    report = o.run(plan)
    assert report.ok

    undo = o.undo_run(plan, report)
    assert undo.reversed == 1
    assert undo.ok and not undo.failed
    assert undo.verified_files == 2          # both restored files re-hashed and matched
    assert all(p.exists() for p in made)
    assert not list(dest.glob("*.pdf"))


def test_session_undo_works_from_persisted_state_alone(policy, workspace, files):
    """`maestro undo` runs in a NEW process: no RunReport, only the plan and the
    variables the episode store kept. The same call must work from those."""
    import json

    made = files("inbox", "a.pdf", "b.pdf", content="original bytes")
    dest = workspace / "archive"
    o = orch(policy)
    plan = move_plan(workspace, made, dest)
    report = o.run(plan)
    assert report.ok

    # Round-trip through JSON exactly as the episode store does.
    persisted_vars = json.loads(json.dumps(report.variables, default=str))
    executed = [s.action_id for s in report.steps if s.ok]

    undo = Orchestrator(policy=policy).undo_run(plan, variables=persisted_vars,
                                                executed=executed)
    assert undo.reversed == 1 and undo.ok
    assert all(p.read_text() == "original bytes" for p in made)


def test_undo_explains_irreversible_and_collided_steps(policy, workspace, files):
    """Undo must SAY what it could not do. A read-only glob has nothing to
    reverse; a newer file at the origin must not be overwritten."""
    made = files("inbox", "a.pdf", content="mine")
    dest = workspace / "archive"
    o = orch(policy)
    plan = plan_of(
        Action(action_id="a1", verb="fs.glob",
               args={"root": str(workspace / "inbox"), "pattern": "*.pdf"},
               produces="found"),
        Action(action_id="a2", verb="fs.move_batch",
               args={"sources": "$found", "dest_dir": str(dest)},
               depends_on=["a1"], produces="moved",
               undo=UndoSpec(verb="fs.restore_manifest", args={"manifest": "$moved"})),
        instruction="move then collide",
    )
    report = o.run(plan)
    assert report.ok
    # Someone puts a NEW file where the original used to be.
    made[0].write_text("newer file, must survive")

    undo = o.undo_run(plan, report)
    assert undo.reversed == 1
    assert undo.collisions == 1
    assert made[0].read_text() == "newer file, must survive"      # not overwritten
    assert (workspace / "inbox" / "a (1).pdf").read_text() == "mine"  # restored beside
    skipped_ids = [aid for aid, _, _ in undo.skipped]
    assert "a1" in skipped_ids                                     # glob: read-only
    assert "read-only" in undo.render()


# =========================================================================== #
# audit trail
# =========================================================================== #


def test_every_transition_is_logged_and_verifiable(policy, workspace, files, audit):
    made = files("inbox", "a.pdf")
    report = orch(policy, audit=audit).run(move_plan(workspace, made, workspace / "out"))
    events = [r.event for r in audit.rows()]
    assert "PROPOSED" in events
    assert "GATED" in events and "APPROVED" in events
    assert "EXECUTED" in events
    assert audit.verify()
    assert report.ok


def test_a_blocked_plan_is_logged_as_blocked(policy, workspace, audit):
    orch(policy, audit=audit).run(plan_of(Action(
        action_id="a1", verb="fs.delete_permanent",
        args={"paths": [str(workspace / "a")]})))
    assert "BLOCKED" in [r.event for r in audit.rows()]
    assert audit.verify()


# =========================================================================== #
# ablation switches
# =========================================================================== #


def test_safety_can_be_disabled_for_the_baseline(policy, workspace):
    """Ablation A1 / baseline B1: everything scores R0 and nothing is gated —
    which is what makes the UER comparison in docs/07 §4 meaningful."""
    asked = []
    gate = ConsentGate(ask=lambda r: asked.append(r) or Approval(True))
    plan = plan_of(Action(action_id="a1", verb="fs.trash",
                          args={"paths": [str(workspace / "nothing")]}))
    report = orch(policy, gate=gate, enable_safety=False).run(plan)
    assert report.verdict.risk is Risk.R0
    assert report.gate == "auto"
    assert asked == []


def test_dry_run_can_be_disabled(policy, workspace, files):
    made = files("inbox", "a.pdf")
    report = orch(policy, enable_dry_run=False).run(
        move_plan(workspace, made, workspace / "out"))
    assert report.status == "completed"
    assert all("dry run disabled" in m.summary for m in report.manifests)


# =========================================================================== #
# the preview
# =========================================================================== #


def test_preview_shows_counts_risk_and_gate(policy, workspace, files):
    made = files("inbox", "a.pdf", "b.pdf")
    captured = {}

    def capture(req):
        captured["text"] = render_preview(req.plan, req.verdict, req.manifests)
        return Approval(True, "click")

    orch(policy, gate=ConsentGate(ask=capture)).run(
        move_plan(workspace, made, workspace / "out"))
    text = captured["text"]
    assert "2 file(s)" in text
    assert "R2" in text and "gate: confirm" in text
    assert "declare an undo" in text


def test_preview_warns_about_collisions(policy, workspace, files):
    made = files("inbox", "a.pdf")
    dest = workspace / "out"
    dest.mkdir()
    (dest / "a.pdf").write_text("already here")
    captured = {}

    def capture(req):
        captured["text"] = render_preview(req.plan, req.verdict, req.manifests)
        return Approval(False, "denied")

    orch(policy, gate=ConsentGate(ask=capture)).run(move_plan(workspace, made, dest))
    assert "collision" in captured["text"]
