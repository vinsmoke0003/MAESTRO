"""The voice interface (`maestro voice`).

No microphone, no speech model, no network: utterances come from ScriptedEars
and replies go to a RecordingMouth, but everything between them is the real
MaestroPipeline — so these tests pin that voice reaches the same NLP, planner,
scorer, consent gate and audit log as typing, and that it cannot get around any
of them.
"""

from __future__ import annotations

import pytest

import maestro.executor  # noqa: F401
from maestro.pipeline import MaestroPipeline
from maestro.voice import (
    Heard,
    RecordingMouth,
    ScriptedEars,
    VoiceAgent,
    is_exit,
    parse_yes_no,
    speakable,
    strip_wake,
)


@pytest.fixture
def sandbox(policy, workspace):
    """Folder names in speech ('inbox', 'archive') resolve inside the test workspace."""
    from maestro.nlp import entities as ents

    saved = dict(ents.FOLDER_ALIASES)
    for alias, sub in [("inbox", "inbox"), ("archive", "archive"),
                       ("downloads", "Downloads"), ("documents", "Documents")]:
        ents.FOLDER_ALIASES[alias] = str(workspace / sub).replace("\\", "/")
        (workspace / sub).mkdir(parents=True, exist_ok=True)
    yield policy
    ents.FOLDER_ALIASES.clear()
    ents.FOLDER_ALIASES.update(saved)


def make_agent(policy, lines, **kw):
    mouth = RecordingMouth()
    shown: list[str] = []
    agent = VoiceAgent(
        ScriptedEars(lines), mouth,
        pipeline_factory=lambda gate: MaestroPipeline(policy=policy, gate=gate),
        show=shown.append, **kw,
    )
    return agent, mouth, shown


def voice(text: str, confidence: float = 0.9) -> Heard:
    return Heard(text, confidence, "voice")


# =========================================================================== #
# parsing what was said
# =========================================================================== #


@pytest.mark.parametrize("text,expected", [
    ("yes", True), ("Yes, go ahead.", True), ("okay do it", True), ("approve", True),
    ("no", False), ("cancel that", False), ("wait", False), ("don't", False),
    ("yes... no wait", False),            # negation wins
    ("sure, why not", False),             # fail safe on anything with a negation
    ("banana", None), ("", None),
])
def test_yes_no_is_strict_and_negation_wins(text, expected):
    assert parse_yes_no(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("goodbye", True), ("Goodbye, Maestro.", True), ("stop listening", True),
    ("quit Spotify", False),               # an app.quit command, not a sign-off
    ("exit the downloads folder", False),
    ("how much battery is left", False),
])
def test_only_a_whole_sign_off_ends_the_session(text, expected):
    assert is_exit(text) is expected


def test_wake_word_is_stripped_and_required():
    assert strip_wake("Hey Maestro, how much disk space is left?", "maestro") \
        == "how much disk space is left?"
    assert strip_wake("maestro", "maestro") == ""
    assert strip_wake("how much disk space is left", "maestro") is None


def test_spoken_paths_become_real_paths():
    from maestro.voice.agent import spoken_to_text

    assert spoken_to_text("Move the PDFs to Documents slash Invoices.") \
        == "Move the PDFs to Documents/Invoices"
    assert spoken_to_text("open report dot pdf") == "open report.pdf"


def test_speech_text_is_short_and_readable():
    s = speakable("Done — 3 step(s) completed -> /Users/me/Documents/Invoices/2024")
    assert "—" not in s and "(s)" not in s and "/Users/me" not in s
    assert "Invoices 2024" in s
    assert len(speakable("word " * 500)) <= 330


# =========================================================================== #
# the conversation, end to end
# =========================================================================== #


def test_a_spoken_read_only_command_runs_and_is_answered_aloud(sandbox):
    agent, mouth, _ = make_agent(sandbox, [voice("how much disk space is left"),
                                           voice("goodbye")])
    assert agent.run() == 0
    assert agent.turns[0].status == "completed"
    assert agent.turns[0].gate == "auto"          # R0 never asks
    assert mouth.said[0].startswith("MAESTRO is listening")
    assert "Done" in mouth.transcript and mouth.said[-1] == "Goodbye."


def test_spoken_yes_approves_an_r2_plan(sandbox, workspace):
    for i in range(3):
        (workspace / "inbox" / f"doc{i}.pdf").write_text("x" * 40)
    agent, mouth, shown = make_agent(sandbox, [
        voice("move the pdfs from inbox to archive"), voice("yes, go ahead")])
    agent.run()
    turn = agent.turns[0]
    assert turn.status == "completed", turn.message
    assert turn.risk == "R2" and turn.report.approval.method == "voice"
    assert len(list((workspace / "archive").glob("*.pdf"))) == 3
    assert "I need your approval" in mouth.transcript
    assert any("MAESTRO will perform" in line for line in shown)   # full preview on screen


def test_spoken_no_or_hesitation_changes_nothing(sandbox, workspace):
    (workspace / "inbox" / "a.pdf").write_text("x")
    agent, _, _ = make_agent(sandbox, [
        voice("move the pdfs from inbox to archive"), voice("yes... no wait")])
    agent.run()
    assert agent.turns[0].status == "denied"
    assert (workspace / "inbox" / "a.pdf").exists()


def test_an_unclear_answer_is_not_consent(sandbox, workspace):
    (workspace / "inbox" / "a.pdf").write_text("x")
    agent, mouth, _ = make_agent(sandbox, [
        voice("move the pdfs from inbox to archive"),
        voice("banana"), voice("yes", confidence=0.1)])    # unclear, then misheard
    agent.run()
    assert agent.turns[0].status == "denied"
    assert (workspace / "inbox" / "a.pdf").exists()
    assert "Please answer yes or no" in mouth.transcript


def test_a_low_confidence_command_is_never_acted_on(sandbox, workspace):
    (workspace / "inbox" / "a.pdf").write_text("x")
    agent, mouth, _ = make_agent(sandbox, [
        voice("move the pdfs from inbox to archive", confidence=0.2), voice("goodbye")])
    agent.run()
    assert agent.turns == []                      # nothing was planned, let alone run
    assert "did not catch that" in mouth.transcript
    assert (workspace / "inbox" / "a.pdf").exists()


def test_unsafe_spoken_requests_are_refused(sandbox, workspace):
    (workspace / "Documents" / "keep.txt").write_text("x")
    agent, mouth, _ = make_agent(sandbox, [
        voice("permanently delete everything in documents"), voice("yes")])
    agent.run()
    assert agent.turns[0].status == "refused"
    assert (workspace / "Documents" / "keep.txt").exists()
    assert "can't do that" in mouth.transcript


def test_a_clarifying_question_is_asked_and_answered_by_voice(sandbox, workspace):
    (workspace / "inbox" / "a.pdf").write_text("x")
    agent, mouth, _ = make_agent(sandbox, [
        voice("move the pdfs from inbox"),          # no destination: must ask
        voice("to the archive folder"), voice("yes")])
    agent.run()
    assert agent.turns[0].status == "completed", agent.turns[0].message
    assert (workspace / "archive" / "a.pdf").exists()
    assert "?" in mouth.said[1]                     # the question was spoken


def test_the_wake_word_gates_every_command(sandbox):
    agent, _, _ = make_agent(sandbox, [
        voice("how much disk space is left"),            # not addressed: ignored
        voice("Maestro, how much disk space is left"),
        voice("goodbye maestro")], wake_word="maestro")
    agent.run()
    assert len(agent.turns) == 1 and agent.turns[0].status == "completed"


# =========================================================================== #
# R3 can never be approved by voice
# =========================================================================== #


class _Req:
    """A minimal typed_confirm request; the preview renderer is stubbed."""

    gate = "typed_confirm"
    token = "CONFIRM 3 FILES"
    plan = verdict = None
    manifests: list = []


def _r3_agent(lines, typed: str, monkeypatch):
    import maestro.voice.agent as agent_mod

    monkeypatch.setattr(agent_mod, "render_preview", lambda *a: "PREVIEW")
    mouth = RecordingMouth()
    agent = VoiceAgent(ScriptedEars(lines), mouth, show=lambda _s: None,
                       typed_input=lambda _p: typed,
                       pipeline_factory=lambda gate: _NoPipe())
    return agent, mouth


class _NoPipe:
    def close(self):
        pass


def test_r3_ignores_a_spoken_yes_and_needs_the_typed_token(monkeypatch):
    agent, mouth = _r3_agent([voice("yes yes confirm")], typed="", monkeypatch=monkeypatch)
    answer = agent._consent(_Req())
    assert not answer.approved
    assert "cannot accept a spoken yes" in mouth.transcript


def test_r3_accepts_the_exact_typed_token(monkeypatch):
    agent, _ = _r3_agent([], typed="confirm 3 files", monkeypatch=monkeypatch)
    answer = agent._consent(_Req())
    assert answer.approved and answer.method == "typed"


def test_the_gate_itself_rejects_a_voice_approval_of_r3():
    """Defence in depth: even a buggy voice layer answering 'voice' for an R3
    plan is refused by ConsentGate, below the voice code."""
    from maestro.safety import Approval, ConsentGate

    class Req:
        gate = "typed_confirm"
        shape = "x:R3"

    gate = ConsentGate(ask=lambda req: Approval(True, "voice"))
    assert not gate.decide(Req()).approved


# =========================================================================== #
# speech output backend + endpointer
# =========================================================================== #


def test_speech_backend_passes_text_on_stdin_not_as_arguments(monkeypatch):
    from maestro.executor.platform import backend

    speech = backend("speech")
    calls = []

    class Proc:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(speech.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(speech.subprocess, "run",
                        lambda cmd, **kw: calls.append((cmd, kw)) or Proc())
    speech.speak("-rf ; rm everything")
    cmd, kw = calls[0]
    assert isinstance(cmd, list)
    assert "-rf ; rm everything" not in " ".join(cmd)
    assert kw["input"] == "-rf ; rm everything"


def test_endpointer_cuts_one_utterance_out_of_silence():
    np = pytest.importorskip("numpy")
    from maestro.voice.vad import FRAME_SAMPLES, SAMPLE_RATE, Endpointer

    rng = np.random.default_rng(0)

    def frames(seconds, level):
        n = int(seconds * SAMPLE_RATE / FRAME_SAMPLES)
        return [(rng.standard_normal(FRAME_SAMPLES) * level).astype("float32")
                for _ in range(n)]

    ep = Endpointer()
    stream = frames(0.6, 0.002) + frames(1.2, 0.08) + frames(1.2, 0.002)
    outs = [u for f in stream if (u := ep.feed(f)) is not None]
    assert len(outs) == 1
    secs = outs[0].size / SAMPLE_RATE
    assert 1.1 <= secs <= 1.9          # the speech, plus pre-roll and a short tail


def test_endpointer_ignores_a_click():
    np = pytest.importorskip("numpy")
    from maestro.voice.vad import FRAME_SAMPLES, Endpointer

    ep = Endpointer()
    quiet = np.zeros(FRAME_SAMPLES, dtype="float32") + 0.001
    loud = np.ones(FRAME_SAMPLES, dtype="float32") * 0.3
    stream = [quiet] * 20 + [loud] * 4 + [quiet] * 40      # ~120 ms burst
    assert all(ep.feed(f) is None for f in stream)


def test_live_captions_and_meter_run_while_speaking_and_final_is_authoritative():
    np = pytest.importorskip("numpy")
    from maestro.voice import MicEars
    from maestro.voice.vad import FRAME_SAMPLES, SAMPLE_RATE

    rng = np.random.default_rng(0)

    def frames(seconds, level):
        n = int(seconds * SAMPLE_RATE / FRAME_SAMPLES)
        return [(rng.standard_normal(FRAME_SAMPLES) * level).astype("float32")
                for _ in range(n)]

    class FakeSTT:
        model_name = "fake"
        calls = 0

        def transcribe(self, audio):
            FakeSTT.calls += 1
            return Heard(f"{audio.size / SAMPLE_RATE:.1f}s of speech", 0.9, "voice")

    captions, levels = [], []

    class FakeMic(MicEars):
        def frames(self):
            import time
            for f in frames(0.6, 0.002) + frames(2.5, 0.08) + frames(1.2, 0.002):
                time.sleep(0.001)          # let the caption worker run
                yield f

    ears = FakeMic(FakeSTT(), on_partial=captions.append,
                   on_level=lambda lv, th, sp: levels.append(sp), partial_every_s=0.5)
    heard = ears.listen()
    assert captions, "no live caption was produced while speaking"
    assert heard.text.endswith("s of speech")      # final transcript from the full clip
    assert True in levels and False in levels      # meter reported both states


def test_a_dc_offset_does_not_deafen_the_endpointer():
    """A real USB mic read 0.43 in a silent room; the start threshold became
    1.32, above full scale, and nothing could ever be heard."""
    np = pytest.importorskip("numpy")
    from maestro.voice.vad import FRAME_SAMPLES, SAMPLE_RATE, Endpointer

    rng = np.random.default_rng(0)

    def frames(seconds, level, dc=0.43):
        n = int(seconds * SAMPLE_RATE / FRAME_SAMPLES)
        return [(dc + rng.standard_normal(FRAME_SAMPLES) * level).astype("float32")
                for _ in range(n)]

    ep = Endpointer()
    stream = frames(0.6, 0.002) + frames(1.2, 0.08) + frames(1.2, 0.002)
    outs = [u for f in stream if (u := ep.feed(f)) is not None]
    assert len(outs) == 1
    assert ep.start_level < 0.1


def test_the_start_threshold_stays_reachable_in_a_loud_room():
    np = pytest.importorskip("numpy")
    from maestro.voice.vad import FRAME_SAMPLES, SAMPLE_RATE, Endpointer

    rng = np.random.default_rng(0)

    def frames(seconds, level):
        n = int(seconds * SAMPLE_RATE / FRAME_SAMPLES)
        return [(rng.standard_normal(FRAME_SAMPLES) * level).astype("float32")
                for _ in range(n)]

    ep = Endpointer()
    stream = frames(0.6, 0.4) + frames(1.2, 0.75) + frames(1.2, 0.4)   # noisy room
    outs = [u for f in stream if (u := ep.feed(f)) is not None]
    assert ep.start_level < 1.0 and ep.stop_level < ep.start_level
    assert len(outs) == 1


def test_push_to_talk_records_between_two_key_presses():
    np = pytest.importorskip("numpy")
    import threading

    from maestro.voice import MicEars
    from maestro.voice.vad import FRAME_SAMPLES

    presses = [threading.Event(), threading.Event()]

    def wait_key(_prompt):
        # first press returns at once; the second arrives after some audio
        if not presses[0].is_set():
            presses[0].set()
            return ""
        presses[1].wait(timeout=5)
        return ""

    class FakeSTT:
        model_name = "fake"

        def transcribe(self, audio):
            return Heard(f"{audio.size} samples", 0.9, "voice")

    class FakeMic(MicEars):
        def frames(self):
            for i in range(200):
                if i == 40:
                    presses[1].set()          # user presses Enter mid-stream
                yield np.full(FRAME_SAMPLES, 0.05, dtype="float32")
                import time
                time.sleep(0.002)

    ears = FakeMic(FakeSTT(), push_to_talk=True, wait_key=wait_key)
    heard = ears.listen()
    n = int(heard.text.split()[0]) // FRAME_SAMPLES
    assert 30 <= n < 200          # stopped by the key press, not the end of the stream


def test_a_command_without_the_wake_word_is_reported_not_silently_dropped(sandbox):
    agent, _, shown = make_agent(sandbox, [voice("how much disk space is left")],
                                 wake_word="maestro")
    agent.run()
    assert agent.turns == []
    assert any("ignored: start with 'Maestro'" in line for line in shown)


def test_found_files_are_listed_on_screen_and_read_without_approval(sandbox, workspace):
    for i in range(30):                                    # above the bulk threshold
        (workspace / "Downloads" / f"paper{i:02d}.pdf").write_text("x")
    agent, mouth, shown = make_agent(sandbox, [voice("find all the pdfs in downloads")])
    agent.run()
    turn = agent.turns[0]
    assert turn.status == "completed", turn.message
    assert turn.gate == "auto"                             # looking never asks
    assert any(line.strip().endswith("paper00.pdf") for line in shown)
    assert sum(1 for line in shown if ".pdf" in line and line.strip()[0].isdigit()) == 30
    assert "I found 30 files" in mouth.transcript
