"""Spoken replies (maestro.voice.speaker), with a fake voice: nothing is really said.

What these pin: it is off by default; it only speaks once switched on and only if
a voice exists; it never blocks the caller; the latest reply wins over one still
waiting; Stop and Off drop what is waiting; a broken voice switches it off instead
of breaking anything; shutdown ends the thread; and the sentence for each kind of
turn (result, plan awaiting approval, question, refusal) is what we expect.
"""

from __future__ import annotations

import threading
import time
import types

import pytest

from maestro.voice.speaker import TEST_SENTENCE, Speaker, spoken_summary


class Mouth:
    """Records what it was asked to say; can be held mid-sentence with `gate`."""

    def __init__(self, gate: threading.Event | None = None, fail: bool = False):
        self.said: list[str] = []
        self.gate = gate
        self.fail = fail
        self.available = True
        self.started = threading.Event()

    def say(self, text):
        self.started.set()
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            raise RuntimeError("audio device vanished")
        self.said.append(text)


@pytest.fixture(autouse=True)
def no_real_voice(monkeypatch):
    from maestro.voice import mouth

    def forbidden(*a, **k):
        raise AssertionError("the real OS voice was used in a test")

    monkeypatch.setattr(mouth.SystemMouth, "say", forbidden)


def _until(pred, timeout=5.0):
    t0 = time.time()
    while not pred() and time.time() - t0 < timeout:
        time.sleep(0.005)
    assert pred()


def make(mouth=None, available=True):
    m = mouth or Mouth()
    return Speaker(mouth_factory=lambda: m, availability=lambda: available), m


def test_off_by_default_and_silent():
    sp, m = make()
    assert sp.status() == {"enabled": False, "available": True, "speaking": False}
    assert sp.say("Done, 2 steps completed.") is False
    time.sleep(0.05)
    assert m.said == [] and sp._thread is None
    assert sp.test()[0] == 409


def test_cannot_switch_on_without_a_voice():
    sp, _ = make(available=False)
    status, out = sp.set_enabled(True)
    assert status == 409 and "No system voice" in out["error"]
    assert sp.status()["enabled"] is False


def test_speaks_cleaned_text_without_blocking():
    gate = threading.Event()
    sp, m = make(Mouth(gate))
    sp.set_enabled(True)
    t0 = time.monotonic()
    assert sp.say("Moved 2 file(s) -> ~/Documents/Invoices/2024 — done") is True
    assert time.monotonic() - t0 < 0.5                       # the caller never waits
    assert sp.status()["speaking"] is True
    gate.set()
    _until(lambda: m.said)
    assert m.said == ["Moved 2 files to Invoices 2024, done"]   # speakable(): short, no path
    _until(lambda: sp.status()["speaking"] is False)


def test_latest_reply_wins_over_one_still_waiting():
    gate = threading.Event()
    sp, m = make(Mouth(gate))
    sp.set_enabled(True)
    sp.say("first")
    m.started.wait(2)                                         # "first" is being spoken
    sp.say("second")
    sp.say("third")                                           # replaces "second"
    gate.set()
    _until(lambda: len(m.said) == 2)
    time.sleep(0.05)
    assert m.said == ["first", "third"]


@pytest.mark.parametrize("how", ["stop", "off"])
def test_stop_and_off_drop_what_is_waiting(how):
    gate = threading.Event()
    sp, m = make(Mouth(gate))
    sp.set_enabled(True)
    sp.say("first")
    m.started.wait(2)
    sp.say("waiting")
    if how == "stop":
        sp.stop()
    else:
        sp.set_enabled(False)
    gate.set()
    _until(lambda: m.said == ["first"])                     # the started sentence finishes
    time.sleep(0.05)
    assert m.said == ["first"]
    assert sp.status()["enabled"] is (how == "stop")


def test_test_sentence_is_fixed():
    sp, m = make()
    sp.set_enabled(True)
    assert sp.test()[0] == 200
    _until(lambda: m.said)
    assert m.said == [TEST_SENTENCE]


@pytest.mark.parametrize("kind", ["raises", "reports_unavailable"])
def test_a_broken_voice_switches_itself_off(kind):
    m = Mouth(fail=(kind == "raises"))
    if kind == "reports_unavailable":
        orig = m.say

        def say(text):
            orig(text)
            m.available = False                              # SystemMouth's own signal
        m.say = say
    sp, _ = make(m)
    sp.set_enabled(True)
    sp.say("hello")
    _until(lambda: sp.status()["enabled"] is False)
    assert sp.status()["available"] is False
    assert sp.say("again") is False
    assert sp.set_enabled(True)[0] == 409


def test_shutdown_ends_the_thread():
    sp, m = make()
    sp.set_enabled(True)
    sp.say("hello")
    _until(lambda: m.said)
    sp.shutdown()
    assert not sp._thread.is_alive()
    assert sp.say("after shutdown") is False


# ---- what is said for each kind of turn --------------------------------------------


def _turn(status, **kw):
    return types.SimpleNamespace(status=status, message=kw.pop("message", ""), **kw)


def test_summary_of_a_result_is_the_outcome_then_the_answer():
    """Same order as the terminal voice agent: what happened, then what was found."""
    t = _turn("completed", message="Done — 1 step(s) completed.",
              report=types.SimpleNamespace(variables={"files": ["/x/a.pdf", "/x/b.pdf"]}))
    s = spoken_summary(t)
    assert s.startswith("Done — 1 step(s) completed. I found 2 files: a, b")


def test_summary_of_a_result_without_an_answer_is_the_outcome():
    t = _turn("completed", message="Done — 2 step(s) completed.",
              report=types.SimpleNamespace(variables={}))
    assert spoken_summary(t) == "Done — 2 step(s) completed."


def test_summary_of_a_plan_waiting_for_approval_says_so():
    plan = types.SimpleNamespace(actions=[1, 2, 3])
    t = _turn("previewed", plan=plan, risk="R2")
    s = spoken_summary(t)
    assert "3 steps, risk R2" in s and "approve it or deny it" in s


@pytest.mark.parametrize("status", ["clarified", "refused", "blocked", "cancelled", "failed"])
def test_summary_of_questions_and_refusals_is_their_message(status):
    assert spoken_summary(_turn(status, message="Which folder?")) == "Which folder?"



# ---- the four wording fixes from the real-voice test ---------------------------------


@pytest.mark.parametrize("text, said", [
    ("Done — 1 step(s) completed.", "Done, 1 step completed."),
    ("Done — 3 step(s) completed, 1 file(s) found.", "Done, 3 steps completed, 1 file found."),
    ("11 step(s) and 21 file(s)", "11 steps and 21 files"),
])
def test_speakable_says_one_step_not_one_steps(text, said):
    from maestro.voice.mouth import speakable

    assert speakable(text) == said


def _ran(verbs_and_files):
    from maestro.orchestrator import RunReport, StepReport

    r = RunReport("p1", status="completed")
    r.steps = [StepReport(f"a{i}", v, True, files_touched=n)
               for i, (v, n) in enumerate(verbs_and_files, 1)]
    return r


@pytest.mark.parametrize("steps, message", [
    ([("fs.glob", 32)], "Done — 1 step completed, 32 files found."),
    ([("search.by_name", 1)], "Done — 1 step completed, 1 file found."),
    ([("fs.glob", 2), ("fs.move_batch", 2)], "Done — 2 steps completed, 4 files touched."),
    ([("sys.info", 0)], "Done — 1 step completed."),
])
def test_outcome_says_found_for_read_only_runs_and_touched_otherwise(steps, message):
    from maestro.pipeline import _outcome_message

    assert _outcome_message(_ran(steps)) == message


def test_time_is_spoken_like_a_person_not_an_epoch():
    from maestro.results import present

    t = _turn("completed", report=types.SimpleNamespace(
        variables={"t": {"local": "2026-10-13T12:03:47", "epoch": 1791873227}}))
    shown = present(t)
    assert shown.spoken == "It's 12:03 PM on Tuesday, 13 October."
    assert "1791873227" not in shown.spoken and "T12" not in shown.spoken
    assert shown.lines == ["  Tuesday, 13 October 2026, 12:03 PM"]


def test_disk_is_spoken_in_plain_words():
    from maestro.results import present

    t = _turn("completed", report=types.SimpleNamespace(variables={"d": {
        "total_gb": 494.38, "used_gb": 458.14, "free_gb": 36.24, "percent_used": 92.7}}))
    shown = present(t)
    assert shown.spoken == "You have 36.2 gigabytes free of 494; 93 percent is used."
    assert shown.lines == ["  36.2 GB free of 494.4 GB (92.7% used)"]


def test_long_file_names_are_not_read_aloud():
    from maestro.results import present

    long = [f"/d/2027 NPN Admission Brochure Final Version {i}.pdf" for i in range(32)]
    shown = present(_turn("completed", report=types.SimpleNamespace(variables={"f": long})))
    assert shown.spoken == "I found 32 files. The full list is on screen."
    assert len(shown.lines) > 10                                   # still all on screen
    short = ["/d/notes.pdf", "/d/invoice.pdf"]
    shown = present(_turn("completed", report=types.SimpleNamespace(variables={"f": short})))
    assert shown.spoken == "I found 2 files: notes, invoice. The full list is on screen."


def test_a_read_only_result_does_not_say_the_count_twice():
    t = _turn("completed", message="Done — 1 step completed, 2 files found.",
              report=types.SimpleNamespace(variables={"f": ["/d/notes.pdf", "/d/invoice.pdf"]}))
    assert spoken_summary(t) == ("Done — 1 step completed. I found 2 files: notes, invoice. "
                                 "The full list is on screen.")
    moved = _turn("completed", message="Done — 2 steps completed, 4 files touched.",
                  report=types.SimpleNamespace(variables={"f": ["/d/a.pdf", "/d/b.pdf"]}))
    assert spoken_summary(moved).startswith("Done — 2 steps completed, 4 files touched. ")
