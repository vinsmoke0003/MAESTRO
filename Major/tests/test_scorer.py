"""The three scorer invariants: deterministic, monotonic, fail-closed.
Plus: the planner's risk hint must never influence the verdict."""

import maestro.executor  # noqa: F401  (registers verbs)
from maestro.ir import Action, Plan, Risk
from maestro.safety import PathPolicy, score_action, score_plan
from maestro.safety.scorer import BULK_N


def _action(**kw) -> Action:
    base = {"action_id": "a1", "verb": "fs.glob", "args": {"root": "~/Downloads"}}
    base.update(kw)
    return Action.model_validate(base)


def _policy(tmp_path):
    return PathPolicy(allow_roots=[str(tmp_path)], deny_dirs=[str(tmp_path / "deny")])


def test_read_only_in_workspace_is_r0(tmp_path):
    v = score_action(_action(args={"root": str(tmp_path / "sub")}), _policy(tmp_path))
    assert v.risk == Risk.R0


def test_move_is_at_least_r2(tmp_path):
    v = score_action(
        _action(verb="fs.move_batch",
                args={"sources": [str(tmp_path / "a.txt")], "dest_dir": str(tmp_path / "b")}),
        _policy(tmp_path),
    )
    assert v.risk == Risk.R2


def test_unknown_verb_fails_closed(tmp_path):
    v = score_action(_action(verb="sys.exec_shell", args={"cmd": "rm -rf /"}), _policy(tmp_path))
    assert v.risk == Risk.BLOCKED  # verb does not exist in the closed registry


def test_hard_blocked_verb_is_blocked_regardless_of_args(tmp_path):
    v = score_action(
        _action(verb="fs.delete_permanent", args={"paths": [str(tmp_path / "x.txt")]}),
        _policy(tmp_path),
    )
    assert v.blocked
    assert "no override" in " ".join(v.reasons)


def test_denylisted_path_blocks(tmp_path):
    v = score_action(
        _action(args={"root": str(tmp_path / "deny" / "inner")}), _policy(tmp_path)
    )
    assert v.risk == Risk.BLOCKED


def test_outside_workspace_escalates_to_r2(tmp_path):
    outside = tmp_path.parent / "somewhere_else"
    v = score_action(_action(args={"root": str(outside)}), _policy(tmp_path))
    assert v.risk == Risk.R2


def test_bulk_escalates(tmp_path):
    a = _action(verb="fs.mkdir", args={"path": str(tmp_path / "d")})
    assert score_action(a, _policy(tmp_path)).risk == Risk.R1
    v = score_action(a, _policy(tmp_path), estimated_files=BULK_N + 1)
    assert v.risk == Risk.R2


def test_risk_hint_is_recorded_but_ignored(tmp_path):
    """A lying planner cannot downgrade risk. This is the core property."""
    lying = _action(
        verb="fs.move_batch",
        args={"sources": [str(tmp_path / "a")], "dest_dir": str(tmp_path / "b")},
        risk_hint=Risk.R0,  # planner claims a move is harmless
    )
    v = score_action(lying, _policy(tmp_path))
    assert v.hint == Risk.R0  # recorded for the hint-agreement metric
    assert v.risk == Risk.R2  # decision unaffected


def test_deterministic(tmp_path):
    a = _action(verb="fs.move_batch",
                args={"sources": [str(tmp_path / "a")], "dest_dir": str(tmp_path / "b")})
    risks = {score_action(a, _policy(tmp_path)).risk for _ in range(100)}
    assert risks == {Risk.R2}


def test_plan_risk_is_max_and_gate_follows(tmp_path):
    plan = Plan.model_validate({
        "plan_id": "p1",
        "instruction": "test",
        "actions": [
            {"action_id": "a1", "verb": "fs.glob",
             "args": {"root": str(tmp_path)}, "produces": "files"},
            {"action_id": "a2", "verb": "fs.move_batch",
             "args": {"sources": "$files", "dest_dir": str(tmp_path / "out")},
             "depends_on": ["a1"]},
        ],
    })
    v = score_plan(plan, _policy(tmp_path))
    assert v.risk == Risk.R2
    assert v.gate == "confirm"


def test_blocked_plan_gate_is_refuse(tmp_path):
    plan = Plan.model_validate({
        "plan_id": "p2",
        "instruction": "delete stuff forever",
        "actions": [{"action_id": "a1", "verb": "fs.delete_permanent",
                     "args": {"paths": [str(tmp_path / "x")]}}],
    })
    v = score_plan(plan, _policy(tmp_path))
    assert v.blocked and v.gate == "refuse"
