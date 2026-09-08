"""Planner, Critic, and the LLM trust boundary.

Every LLM test uses `ScriptedClient`, so the suite runs in milliseconds on a
machine with no Ollama, no GPU and no network. That is a requirement, not a
convenience: CI has none of those.
"""

from __future__ import annotations

import json

import pytest

import maestro.executor  # noqa: F401
from maestro import registry
from maestro.ir import Risk
from maestro.llm.fake import FailingClient, ScriptedClient
from maestro.nlp import EntityExtractor, RuleIntentClassifier
from maestro.nlp.intents import APP_LAUNCH, FILE_ORGANIZE, FILE_SEARCH
from maestro.planner import Critic, HybridPlanner, NoTemplate, Planner, PlannerError, prompts
from maestro.planner.rulebased import build_plan
from maestro.safety import PathPolicy, score_plan

GOOD_PLAN = json.dumps({"actions": [
    {"action_id": "a1", "verb": "fs.glob",
     "args": {"root": "~/Downloads", "pattern": "*.pdf"},
     "depends_on": [], "produces": "matched", "risk_hint": "R0",
     "rationale": "Find the PDFs"},
    {"action_id": "a2", "verb": "fs.move_batch",
     "args": {"sources": "$matched", "dest_dir": "~/Documents/Invoices"},
     "depends_on": ["a1"], "produces": "moved", "risk_hint": "R2",
     "rationale": "Move them"},
]})


def slots_for(text: str):
    return EntityExtractor().slots(text, RuleIntentClassifier().predict(text).intent)


# =========================================================================== #
# the decoding contract
# =========================================================================== #


def test_decode_schema_verb_enum_is_the_registry():
    """The model cannot emit an unregistered verb because the grammar it is
    decoded against does not contain one (docs/03 §2)."""
    schema = prompts.decode_schema()
    enum = schema["properties"]["actions"]["items"]["properties"]["verb"]["enum"]
    assert set(enum) == set(registry.plannable_verbs())


def test_hard_blocked_verbs_are_not_in_the_decode_schema():
    schema = prompts.decode_schema()
    enum = schema["properties"]["actions"]["items"]["properties"]["verb"]["enum"]
    for verb in registry.hard_blocked_verbs():
        assert verb not in enum


def test_schema_forces_explicit_dataflow():
    """`produces` and `depends_on` are required so the model must state its
    dataflow; qwen2.5 otherwise omits them and emits dangling $refs."""
    required = prompts.decode_schema()["properties"]["actions"]["items"]["required"]
    assert "produces" in required and "depends_on" in required


def test_the_planner_has_no_channel_for_untrusted_content():
    """docs/02 §6 rule 1, asserted structurally rather than by string search.

    The planner takes an instruction, an intent and slots. There is no parameter
    through which file contents or page text could arrive, so "T2 never enters
    the planner's context" is a property of the signature, not a convention
    someone has to remember. If a `context=` or `documents=` parameter is ever
    added here, this test is what should stop it.
    """
    import inspect

    params = set(inspect.signature(Planner.plan).parameters) - {"self"}
    assert params == {"instruction", "intent", "slots"}

    params = set(inspect.signature(prompts.user_prompt).parameters)
    assert not params & {"content", "document", "page_text", "file_contents"}


def test_prompt_carries_only_trusted_material():
    """The rendered prompt contains the instruction and MAESTRO's own metadata.

    The verb descriptions do mention the word "untrusted" — that is the registry
    telling the planner which verbs *return* untrusted output, which is exactly
    the information it should have. What must never appear is the content
    itself, so the assertion is on the document framing the Summarizer uses.
    """
    system = prompts.system_prompt()
    user = prompts.user_prompt("move the pdfs", "FILE_ORGANIZE",
                               slots_for("move the pdfs from Downloads to Documents"))
    combined = system + user
    assert "BEGIN DOCUMENT" not in combined
    assert "END DOCUMENT" not in combined
    assert "move the pdfs" in combined


def test_training_and_inference_share_the_same_system_prompt():
    """A format mismatch between training and inference is the most common
    cause of "the fine-tune made it worse" (docs/05 §3)."""
    system_train, _ = prompts.training_prompt("x", {"platform": "darwin"})
    assert system_train == prompts.system_prompt()


# =========================================================================== #
# the LLM planner
# =========================================================================== #


def test_planner_builds_a_valid_plan():
    planner = Planner(ScriptedClient(responses=[GOOD_PLAN]))
    plan = planner.plan("move the pdfs from Downloads to Documents/Invoices")
    assert plan.verb_sequence() == ["fs.glob", "fs.move_batch"]
    assert plan.planner.strategy == "llm"


def test_the_model_does_not_get_to_write_the_instruction():
    """plan_id and the instruction are bound by us, so a plan is always
    traceable to the exact user input."""
    planner = Planner(ScriptedClient(responses=[GOOD_PLAN]))
    plan = planner.plan("MY EXACT WORDS")
    assert plan.instruction == "MY EXACT WORDS"
    assert plan.plan_id.startswith("p_")


def test_repair_loop_feeds_the_error_back_and_recovers():
    broken = json.dumps({"actions": [
        {"action_id": "a1", "verb": "fs.move_batch",
         "args": {"sources": "$never_produced", "dest_dir": "~/Documents"},
         "depends_on": [], "produces": None, "rationale": "oops"}]})
    client = ScriptedClient(responses=[broken, GOOD_PLAN])
    plan = Planner(client).plan("move the pdfs")
    assert plan.verb_sequence() == ["fs.glob", "fs.move_batch"]
    assert len(client.calls) == 2
    # The second prompt must actually contain the validator's complaint.
    assert "rejected by the validator" in client.calls[1][1]


def test_repair_is_bounded_and_fails_cleanly():
    """FR-11: no silent retries, no partial plans."""
    planner = Planner(ScriptedClient(responses=["not json at all"]))
    with pytest.raises(PlannerError, match="no schema-valid plan after 3"):
        planner.plan("do something")
    assert len(planner.attempts) == 3


def test_unknown_verb_from_the_model_is_rejected():
    """Constrained decoding should make this unreachable — the planner must
    not RELY on the decoder."""
    rogue = json.dumps({"actions": [
        {"action_id": "a1", "verb": "sys.exec_shell", "args": {"cmd": "rm -rf /"},
         "depends_on": [], "produces": None, "rationale": "hi"}]})
    with pytest.raises(PlannerError):
        Planner(ScriptedClient(responses=[rogue])).plan("clean up")


def test_hard_blocked_verb_from_the_model_is_rejected():
    blocked = json.dumps({"actions": [
        {"action_id": "a1", "verb": "email.send",
         "args": {"to": ["a@b.c"], "subject": "s", "body": "b"},
         "depends_on": [], "produces": None, "rationale": "send it"}]})
    with pytest.raises(PlannerError):
        Planner(ScriptedClient(responses=[blocked])).plan("email the report")


def test_planner_tolerates_markdown_fences():
    planner = Planner(ScriptedClient(responses=[f"```json\n{GOOD_PLAN}\n```"]))
    assert planner.plan("move the pdfs").verb_sequence() == ["fs.glob", "fs.move_batch"]


def test_provider_outage_surfaces_as_a_planner_error():
    with pytest.raises(PlannerError, match="LLM unavailable"):
        Planner(FailingClient()).plan("move the pdfs")


def test_risk_hint_is_carried_through_but_not_trusted():
    plan = Planner(ScriptedClient(responses=[GOOD_PLAN])).plan("move the pdfs")
    assert plan.action("a1").risk_hint is Risk.R0
    assert plan.action("a2").risk_hint is Risk.R2


# =========================================================================== #
# the hybrid planner — why a clean clone works
# =========================================================================== #


def test_hybrid_uses_the_llm_when_it_works():
    h = HybridPlanner(llm=Planner(ScriptedClient(responses=[GOOD_PLAN])))
    text = "move the pdfs from Downloads to Documents/Invoices"
    h.plan(text, FILE_ORGANIZE, slots_for(text))
    assert h.last_strategy == "llm"


def test_hybrid_falls_back_to_templates_when_the_llm_fails():
    h = HybridPlanner(llm=Planner(FailingClient()))
    text = "move the pdfs from Downloads to Documents/Invoices"
    plan = h.plan(text, FILE_ORGANIZE, slots_for(text))
    assert h.last_strategy == "rule"
    assert plan.verb_sequence() == ["fs.glob", "fs.mkdir", "fs.move_batch"]
    assert h.last_fallback_reason


def test_hybrid_with_no_llm_at_all():
    text = "open Chrome"
    plan = HybridPlanner(llm=None).plan(text, APP_LAUNCH, slots_for(text))
    assert plan.verb_sequence() == ["app.launch"]


def test_hybrid_reports_honestly_when_neither_can_plan():
    h = HybridPlanner(llm=None)
    with pytest.raises(PlannerError, match="no template"):
        h.plan("move my files", FILE_ORGANIZE, slots_for("move my files"))


# =========================================================================== #
# the deterministic templates (baseline B0 + gold-plan generator)
# =========================================================================== #


def test_templates_produce_plans_the_scorer_accepts():
    """Gold plans and the rule baseline come from this code, so a template that
    the scorer would reject could never reach the dataset."""
    policy = PathPolicy()
    cases = [
        ("move all pdfs from Downloads to Documents/Invoices", FILE_ORGANIZE),
        ("find the presentations in Documents", FILE_SEARCH),
        ("open Chrome", APP_LAUNCH),
    ]
    for text, intent in cases:
        plan = build_plan(text, intent, slots_for(text))
        assert not score_plan(plan, policy).blocked, text


def test_delete_routes_to_trash_never_unlink():
    """FR-26. `fs.delete_permanent` is not reachable from any template."""
    text = "delete the screenshots on my Desktop"
    plan = build_plan(text, "FILE_DELETE", slots_for(text))
    assert "fs.trash" in plan.verb_sequence()
    assert "fs.delete_permanent" not in plan.verb_sequence()


def test_move_declares_its_undo():
    text = "move all pdfs from Downloads to Documents/Invoices"
    plan = build_plan(text, FILE_ORGANIZE, slots_for(text))
    move = next(a for a in plan.actions if a.verb == "fs.move_batch")
    assert move.undo is not None
    assert move.undo.verb == "fs.restore_manifest"


def test_templates_declare_postconditions():
    text = "move all pdfs from Downloads to Documents/Invoices"
    plan = build_plan(text, FILE_ORGANIZE, slots_for(text))
    assert all(a.postconditions for a in plan.actions)


def test_organise_by_type_fans_out_per_extension():
    text = "organise Downloads by file type"
    plan = build_plan(text, FILE_ORGANIZE, slots_for(text))
    assert len(plan.actions) >= 10
    assert plan.verb_sequence().count("fs.move_batch") >= 5


def test_template_refuses_to_guess_a_missing_slot():
    from maestro.nlp.entities import Slots

    with pytest.raises(NoTemplate):
        build_plan("move my files", FILE_ORGANIZE, Slots())


# =========================================================================== #
# the Critic (threat T2)
# =========================================================================== #


def test_critic_is_quiet_on_a_faithful_plan():
    text = "move all pdfs from Downloads to Documents/Invoices"
    s = slots_for(text)
    plan = build_plan(text, FILE_ORGANIZE, s)
    assert Critic().review(plan, FILE_ORGANIZE, s, text).clean


def test_critic_flags_a_destructive_verb_nobody_asked_for():
    """"Find my PDFs" that also trashes things is not unsafe by the scorer's
    rules — every action is reversible and in-workspace — and is still wrong."""
    text = "find the pdfs in Downloads"
    s = slots_for(text)
    plan = build_plan("delete the pdfs in Downloads", "FILE_DELETE", s)
    report = Critic().review(plan, FILE_SEARCH, s, text)
    assert not report.clean
    assert any(f.code in ("C1", "C2") for f in report.findings)
    assert report.serious


def test_critic_flags_untrusted_content_feeding_a_write():
    from maestro.ir import Action, Plan

    plan = Plan(plan_id="p", instruction="summarise the notes", actions=[
        Action(action_id="a1", verb="fs.read_text", args={"path": "~/Documents/n.txt"},
               produces="content"),
        Action(action_id="a2", verb="fs.write_text",
               args={"path": "~/Documents/out.txt", "content": "$content"},
               depends_on=["a1"]),
    ])
    report = Critic().review(plan, "FILE_READ", None, "summarise the notes")
    assert any(f.code == "C6" for f in report.findings)


def test_critic_findings_render_for_the_preview():
    text = "find the pdfs in Downloads"
    s = slots_for(text)
    plan = build_plan("delete the pdfs in Downloads", "FILE_DELETE", s)
    rendered = Critic().review(plan, FILE_SEARCH, s, text).render()
    assert "Critic findings" in rendered
