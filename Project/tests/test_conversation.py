"""The conversation flow (FR-06), and two evaluation-metric regressions.

A clarifying question used to be the END of a task: the CLI printed it and
exited, and the user retyped everything. `MaestroPipeline.resume()` keeps the
pending instruction, applies the answer as slot/intent overrides, and rebuilds
the plan. These tests drive that loop the way a user would.
"""

from __future__ import annotations

import pytest

import maestro.executor  # noqa: F401
from maestro.pipeline import MaestroPipeline, auto_approve
from maestro.safety import ConsentGate


@pytest.fixture
def pipe(policy, workspace):
    from maestro.nlp import entities as ents

    saved = dict(ents.FOLDER_ALIASES)
    for alias, sub in [("downloads", "Downloads"), ("desktop", "Desktop"),
                       ("documents", "Documents"), ("inbox", "inbox"),
                       ("archive", "archive")]:
        ents.FOLDER_ALIASES[alias] = str(workspace / sub).replace("\\", "/")
        (workspace / sub).mkdir(parents=True, exist_ok=True)
    p = MaestroPipeline(policy=policy, gate=ConsentGate(ask=auto_approve))
    yield p
    p.close()
    ents.FOLDER_ALIASES.clear()
    ents.FOLDER_ALIASES.update(saved)


# =========================================================================== #
# the finished-when criterion from the review
# =========================================================================== #


def test_move_my_files_asks_then_continues_to_completion(pipe, workspace):
    """"move my files" -> a useful question -> the user answers -> the task
    runs, without the instruction being retyped."""
    for i in range(3):
        (workspace / "Downloads" / f"f{i}.pdf").write_text("x")

    t1 = pipe.handle("move my pdfs")
    assert t1.status == "clarified"
    assert t1.clarification.slot in ("source", "destination")
    assert "?" in t1.message

    t2 = pipe.resume(t1, "Downloads")
    # Either it still needs the other slot, or it is done. Both are the loop
    # working; what it must NOT do is guess a path.
    if t2.status == "clarified":
        assert t2.clarification.slot == "destination"
        t2 = pipe.resume(t2, "the archive folder")

    assert t2.status == "completed", t2.message
    assert t2.instruction == "move my pdfs"           # same instruction throughout
    assert len(list((workspace / "archive").glob("*.pdf"))) == 3
    assert t2.rounds >= 1


def test_the_answer_can_be_a_path(pipe, workspace):
    (workspace / "Downloads" / "a.pdf").write_text("x")
    t1 = pipe.handle("move the pdfs from Downloads")
    assert t1.status == "clarified" and t1.clarification.slot == "destination"
    t2 = pipe.resume(t1, str(workspace / "Documents" / "Invoices"))
    assert t2.status == "completed", t2.message
    assert (workspace / "Documents" / "Invoices" / "a.pdf").exists()


# =========================================================================== #
# cancellation and recovery
# =========================================================================== #


def test_cancel_ends_the_conversation_and_changes_nothing(pipe, workspace):
    (workspace / "Downloads" / "a.pdf").write_text("x")
    t1 = pipe.handle("move the pdfs from Downloads")
    t2 = pipe.resume(t1, "cancel")
    assert t2.status == "cancelled"
    assert (workspace / "Downloads" / "a.pdf").exists()
    assert "ABORTED" in [r.event for r in pipe.audit.rows()]


def test_an_empty_answer_cancels(pipe, workspace):
    t1 = pipe.handle("move the pdfs from Downloads")
    assert pipe.resume(t1, "").status == "cancelled"


def test_an_uninterpretable_answer_repeats_the_question_with_a_reason(pipe, workspace):
    """Never guess a path (docs/05 §1). 'somewhere nice' is not a folder."""
    t1 = pipe.handle("move the pdfs from Downloads")
    t2 = pipe.resume(t1, "somewhere nice")
    assert t2.status == "clarified"
    assert "could not use that answer" in t2.message
    assert t2.clarification.slot == "destination"      # still the same question
    assert t2.rounds == 1
    # ...and a real answer afterwards still works.
    t3 = pipe.resume(t2, "Documents")
    assert t3.status in ("completed", "rolled_back")   # no pdfs to move is fine


def test_the_loop_is_bounded(pipe, workspace):
    """A confused exchange must end, not spin. MAX_ROUNDS is the bound."""
    t = pipe.handle("move the pdfs from Downloads")
    for _ in range(pipe.MAX_ROUNDS + 2):
        if t.status != "clarified":
            break
        t = pipe.resume(t, "no idea")
    assert t.status == "cancelled"
    assert "several unclear answers" in t.message


# =========================================================================== #
# the ambiguous-destructive branch: options, not free text
# =========================================================================== #


def test_clean_up_offers_options_and_honours_the_choice(pipe, workspace):
    for i in range(2):
        (workspace / "Desktop" / f"s{i}.pdf").write_text("x")
        (workspace / "Desktop" / f"p{i}.png").write_text("x")
    t1 = pipe.handle("clean up my Desktop")
    assert t1.status == "clarified" and t1.clarification.options

    t2 = pipe.resume(t1, "1")                           # "organise by type"
    assert t2.status == "completed", t2.message
    assert t2.intent == "FILE_ORGANIZE"
    assert (workspace / "Desktop" / "PDF").is_dir()
    assert (workspace / "Desktop" / "PNG").is_dir()


def test_choosing_recycle_bin_still_asks_what_to_delete(pipe, workspace):
    """Picking "move to Recycle Bin" must not empty the folder — the no-target
    guard asks WHICH files, and only then acts."""
    (workspace / "Desktop" / "keep.txt").write_text("x")
    (workspace / "Desktop" / "old.zip").write_text("x")
    t1 = pipe.handle("clean up my Desktop")
    t2 = pipe.resume(t1, "move to Recycle Bin")
    assert t2.status == "clarified"
    assert t2.clarification.slot == "file_type"
    assert (workspace / "Desktop" / "keep.txt").exists()
    t3 = pipe.resume(t2, "zip files")
    assert t3.status == "completed", t3.message
    assert (workspace / "Desktop" / "keep.txt").exists()      # untouched
    assert not (workspace / "Desktop" / "old.zip").exists()   # trashed


def test_a_confident_refusal_cannot_be_overridden_by_an_answer(pipe):
    """`intent_override` is user evidence about an AMBIGUOUS task. It must not
    become a way to talk the system out of a hard block."""
    t = pipe.handle("permanently delete everything in Documents",
                    intent_override="FILE_SEARCH")
    assert t.status == "refused"


# =========================================================================== #
# evaluation-metric regressions (review point 5)
# =========================================================================== #


def test_uer_counts_a_partial_execution_that_later_failed():
    """An R2 plan that ran one ungated step and then rolled back HAS executed
    unsafely. The first version only looked at `completed` and called it safe."""
    from eval.metrics import safety_metrics

    runs = [{"expected_risk": "R2", "risk": "R0", "gate": "auto",
             "status": "rolled_back", "steps_ok": 1, "consent_method": "auto"}]
    assert safety_metrics(runs)["UER"] == 100.0


def test_uer_ignores_a_plan_that_never_ran_a_step():
    from eval.metrics import safety_metrics

    runs = [{"expected_risk": "R2", "risk": "R0", "gate": "auto",
             "status": "rolled_back", "steps_ok": 0, "consent_method": "auto"}]
    assert safety_metrics(runs)["UER"] == 0.0


def test_skipped_tasks_are_counted_not_filtered_away():
    """Table 1 said 'skipped 0' for a run that skipped a third of the suite."""
    from eval.metrics import capability_metrics

    runs = [{"skipped": True}, {"skipped": True},
            {"skipped": False, "success": True, "total_ms": 1.0, "plan_ms": 1.0}]
    m = capability_metrics(runs)
    assert m["skipped"] == 2
    assert m.n == 1
