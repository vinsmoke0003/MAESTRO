"""Planner tests with a fake LLM client — no Ollama required.

What must hold regardless of which model sits behind the interface:
- valid model output -> validated Plan with OUR plan_id and OUR instruction
- invalid output -> repair loop with the validator error fed back, bounded
- a model emitting unknown verbs / bad DAGs cannot produce a Plan at all
"""

import json

import pytest

import maestro.executor  # noqa: F401  (registers verbs)
from maestro.planner import Planner, PlannerError
from maestro.planner.planner import MAX_ATTEMPTS


class FakeClient:
    """Returns queued responses; records every prompt it was sent."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, *, schema: dict | None = None) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise AssertionError("FakeClient exhausted")
        return self.responses.pop(0)


GOOD = json.dumps({
    "actions": [
        {"action_id": "a1", "verb": "fs.glob",
         "args": {"root": "~/maestro_workspace/inbox", "pattern": "*.pdf"},
         "produces": "pdfs", "rationale": "find pdfs"},
        {"action_id": "a2", "verb": "fs.move_batch",
         "args": {"sources": "$pdfs", "dest_dir": "~/maestro_workspace/archive"},
         "depends_on": ["a1"], "rationale": "move them"},
    ]
})


def test_valid_output_becomes_plan():
    planner = Planner(FakeClient([GOOD]))
    plan = planner.plan("move the pdfs from inbox to archive")
    assert plan.instruction == "move the pdfs from inbox to archive"  # bound by us
    assert plan.plan_id.startswith("p_")
    assert [a.verb for a in plan.actions] == ["fs.glob", "fs.move_batch"]
    assert plan.topo_order == ["a1", "a2"]


def test_repair_loop_recovers_and_feeds_error_back():
    bad = json.dumps({"actions": [
        {"action_id": "a1", "verb": "fs.move_batch",
         "args": {"sources": "$ghost", "dest_dir": "~/x"}, "rationale": "broken"},
    ]})
    client = FakeClient([bad, GOOD])
    plan = Planner(client).plan("move stuff")
    assert len(client.calls) == 2
    assert "$ghost" in client.calls[1][1] or "ghost" in client.calls[1][1]  # error fed back
    assert len(plan.actions) == 2


def test_gives_up_after_max_attempts():
    client = FakeClient(["not json at all"] * MAX_ATTEMPTS)
    with pytest.raises(PlannerError, match=f"after {MAX_ATTEMPTS} attempts"):
        Planner(client).plan("do something")
    assert len(client.calls) == MAX_ATTEMPTS


def test_unknown_verb_cannot_become_plan():
    evil = json.dumps({"actions": [
        {"action_id": "a1", "verb": "sys.exec_shell",
         "args": {"cmd": "rm -rf /"}, "rationale": "hax"},
    ]})
    client = FakeClient([evil] * MAX_ATTEMPTS)
    with pytest.raises(PlannerError):
        Planner(client).plan("clean my disk")
    # NOTE: even if this Plan had constructed, the deterministic scorer
    # blocks unknown verbs — defense in depth (tests/test_scorer.py).


def test_hard_blocked_verbs_not_advertised_to_model():
    client = FakeClient([GOOD])
    Planner(client).plan("anything")
    system_prompt = client.calls[0][0]
    assert "fs.delete_permanent" not in system_prompt  # exists only to be refused
    assert "fs.move_batch" in system_prompt
