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


# =========================================================================== #
# GUI push-to-talk capture (fake microphone, fake transcriber)
# =========================================================================== #

class _Mic:
    """A fake MicEars: frames() is a generator whose `finally` marks the mic released."""

    def __init__(self, reg, *, heard=None, fail=None, frames=None, level=0.05):
        self.reg, self.fail, self.limit, self.level = reg, fail, frames, level
        self.transcriber = self
        self.heard = heard if heard is not None else Heard("move the pdfs to documents",
                                                           0.9, "voice")

    def frames(self):
        import time

        import numpy as np

        from maestro.voice.vad import FRAME_SAMPLES

        self.reg.opened += 1
        try:
            if self.fail:
                raise self.fail
            n = 0
            while True:
                if self.limit is not None and n >= self.limit:
                    time.sleep(0.01)
                    yield None
                    continue
                n += 1
                time.sleep(0.002)
                wave = np.where(np.arange(FRAME_SAMPLES) % 2, self.level, -self.level)
                yield wave.astype("float32")                # rms() ignores a DC offset
        finally:
            self.reg.closed += 1

    def transcribe(self, audio):
        self.reg.transcribed.append(int(audio.size))
        if isinstance(self.heard, Exception):
            raise self.heard
        return self.heard


@pytest.fixture
def fake_voice(monkeypatch):
    """No real microphone, no Whisper model, no network."""
    import socket
    import types

    from maestro.voice import capture, ears

    def forbidden(*a, **k):
        raise AssertionError("real microphone / speech model / network used in a test")

    monkeypatch.setattr(ears.MicEars, "frames", forbidden)
    monkeypatch.setattr(ears.MicEars, "_sd", staticmethod(forbidden))
    monkeypatch.setattr(ears.WhisperTranscriber, "load", forbidden)
    real_create = socket.create_connection
    monkeypatch.setattr(socket, "create_connection",
                        lambda a, *x, **k: real_create(a, *x, **k)
                        if a[0] in ("127.0.0.1", "localhost") else forbidden())
    monkeypatch.setattr(capture, "_missing_deps", lambda: [])
    monkeypatch.setattr(capture.VoiceCapture, "_describe_device", lambda self: "Fake Mic")
    reg = types.SimpleNamespace(opened=0, closed=0, transcribed=[])

    def make(**kw):
        return capture.VoiceCapture(ears_factory=lambda: _Mic(reg, **{
            k: v for k, v in kw.items() if k != "max_seconds"}),
            **({"max_seconds": kw["max_seconds"]} if "max_seconds" in kw else {}))

    reg.make = make
    return reg


def _settle(cap, timeout=5.0):
    import time

    from maestro.voice.capture import BUSY

    t0 = time.time()
    while time.time() - t0 < timeout:
        st = cap.status()
        if st["state"] not in BUSY:
            return st
        time.sleep(0.01)
    raise AssertionError("capture did not settle")


def _until(cap, state, timeout=5.0):
    import time

    t0 = time.time()
    while time.time() - t0 < timeout:
        if cap.status()["state"] == state:
            return
        time.sleep(0.005)
    raise AssertionError(f"never reached {state}")


def test_capture_status_never_opens_the_microphone(fake_voice):
    cap = fake_voice.make()
    for _ in range(3):
        st = cap.status()
    assert st["state"] == "idle" and st["device"] == "Fake Mic" and st["available"]
    assert fake_voice.opened == 0


def test_capture_records_until_stop_then_transcribes_in_memory(fake_voice, tmp_path):
    import json

    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    cap = fake_voice.make()
    assert cap.start()[0] == 202
    _recorded(cap)
    assert cap.status()["level_pct"] > 0
    assert cap.start()[0] == 409                        # only one recording at a time
    assert cap.stop()[0] == 202
    st = _settle(cap)
    assert st["state"] == "transcript_ready"
    assert st["transcript"] == "move the pdfs to documents" and st["confidence"] == 0.9
    assert st["low_confidence"] is False
    assert fake_voice.opened == 1 and fake_voice.closed == 1        # mic released
    assert fake_voice.transcribed and fake_voice.transcribed[0] > 0
    text = json.dumps(st)
    assert len(text) < 1500 and "audio" not in st                   # no samples returned
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before  # no files
    assert cap.stop()[0] == 409                                     # stop when not recording


def test_capture_time_limit_releases_the_microphone(fake_voice):
    cap = fake_voice.make(max_seconds=0.3)
    cap.start()
    st = _settle(cap)
    assert st["state"] == "transcript_ready" and "limit" in st["message"]
    assert fake_voice.closed == 1


def test_capture_empty_and_silent_recordings_fail_politely(fake_voice):
    cap = fake_voice.make(frames=2)                      # 60 ms: a click, not speech
    cap.start()
    _until(cap, "listening")
    cap.stop()
    st = _settle(cap)
    assert st["state"] == "failed" and "No audio" in st["message"]
    assert fake_voice.closed == 1 and fake_voice.transcribed == []

    cap = fake_voice.make(heard=Heard("", 0.0, "voice"))
    cap.start()
    _recorded(cap)
    cap.stop()
    st = _settle(cap)
    assert st["state"] == "failed" and "No speech" in st["message"]
    assert st["transcript"] == ""


def test_capture_low_confidence_is_flagged_not_hidden(fake_voice):
    cap = fake_voice.make(heard=Heard("muve the pdf", 0.2, "voice"))
    cap.start()
    _recorded(cap)
    cap.stop()
    st = _settle(cap)
    assert st["state"] == "transcript_ready" and st["low_confidence"] is True
    assert "Low confidence" in st["message"] and st["transcript"] == "muve the pdf"


@pytest.mark.parametrize("error, expected", [
    ("the microphone did not start within 5 s. On macOS ...", "Allow microphone access"),
    ("could not open the microphone: PortAudioError -9986 /dev/cu.mic", "No microphone"),
    ("microphone access needs sounddevice: pip install", "voice extras"),
])
def test_capture_microphone_failures_are_generic(fake_voice, error, expected):
    from maestro.voice import VoiceUnavailable

    cap = fake_voice.make(fail=VoiceUnavailable(error))
    cap.start()
    st = _settle(cap)
    assert st["state"] == "failed" and expected in st["message"]
    for leak in ("PortAudio", "/dev", "-9986", "within 5 s"):
        assert leak not in st["message"]
    assert fake_voice.closed == 1


def test_capture_transcription_failure_is_generic(fake_voice):
    cap = fake_voice.make(heard=RuntimeError("/Users/me/.cache/whisper model.bin corrupt"))
    cap.start()
    _recorded(cap)
    cap.stop()
    st = _settle(cap)
    assert st["state"] == "failed" and "Speech recognition failed" in st["message"]
    assert "/Users" not in st["message"] and "corrupt" not in st["message"]


def test_capture_discard_clears_and_releases(fake_voice):
    cap = fake_voice.make()
    cap.start()
    _until(cap, "listening")
    assert cap.discard()[1]["state"] in ("stopping", "idle")    # discard while recording
    _until(cap, "idle")
    assert fake_voice.closed == 1
    assert fake_voice.transcribed == []                 # never transcribed

    cap.start()
    _recorded(cap)
    cap.stop()
    assert _settle(cap)["state"] == "transcript_ready"
    st = cap.discard()[1]
    assert st["state"] == "idle" and st["transcript"] == "" and st["confidence"] is None


def test_capture_shutdown_during_recording_releases_the_microphone(fake_voice):
    cap = fake_voice.make()
    cap.start()
    _until(cap, "listening")
    cap.shutdown()
    assert fake_voice.closed == 1 and cap.status()["state"] == "idle"


def test_capture_reports_unavailable_without_voice_libraries(fake_voice, monkeypatch):
    from maestro.voice import capture

    monkeypatch.setattr(capture, "_missing_deps", lambda: ["faster-whisper"])
    cap = fake_voice.make()
    st = cap.status()
    assert st["state"] == "unavailable" and st["missing"] == ["faster-whisper"]
    status, out = cap.start()
    assert status == 409 and "pip install" in out["error"] and fake_voice.opened == 0


def test_missing_libraries_override_a_previous_failure(fake_voice, monkeypatch):
    from maestro.voice import VoiceUnavailable, capture

    cap = fake_voice.make(fail=VoiceUnavailable("could not open the microphone: x"))
    cap.start()
    assert _settle(cap)["state"] == "failed"
    monkeypatch.setattr(capture, "_missing_deps", lambda: ["sounddevice"])
    st = cap.status()
    assert st["state"] == "unavailable" and "pip install" in st["message"]



# --------------------------------------------------------------------------- #
# discard while a worker is still running: never two workers
# --------------------------------------------------------------------------- #

class _GatedMic:
    """Deterministic fake: releasing the mic waits on `release`, transcription waits on
    `transcribe_gate`; counts live microphones and concurrent transcriptions (with peaks).
    """

    def __init__(self, reg, heard=None):
        self.reg = reg
        self.transcriber = self
        self.heard = heard or Heard("delete my downloads", 0.95, "voice")

    def frames(self):
        import numpy as np

        from maestro.voice.vad import FRAME_SAMPLES

        with self.reg.lock:
            self.reg.mics += 1
            self.reg.peak_mics = max(self.reg.peak_mics, self.reg.mics)
            self.reg.opened += 1
        try:
            while True:
                yield np.where(np.arange(FRAME_SAMPLES) % 2, 0.05, -0.05).astype("float32")
                self.reg.frame_tick.wait(0.002)
        finally:
            self.reg.release.wait(5)                 # a slow microphone release
            with self.reg.lock:
                self.reg.mics -= 1

    def transcribe(self, audio):
        with self.reg.lock:
            self.reg.tx += 1
            self.reg.peak_tx = max(self.reg.peak_tx, self.reg.tx)
        try:
            self.reg.transcribe_gate.wait(5)
            if isinstance(self.heard, Exception):
                raise self.heard
            return self.heard
        finally:
            with self.reg.lock:
                self.reg.tx -= 1


@pytest.fixture
def gated(fake_voice):
    import threading
    import types

    from maestro.voice import capture

    reg = types.SimpleNamespace(lock=threading.Lock(), mics=0, peak_mics=0, opened=0, tx=0,
                                peak_tx=0, release=threading.Event(),
                                transcribe_gate=threading.Event(), frame_tick=threading.Event(),
                                heard=None)
    reg.release.set()
    reg.transcribe_gate.set()
    reg.cap = capture.VoiceCapture(ears_factory=lambda: _GatedMic(reg, reg.heard))
    yield reg
    reg.release.set()
    reg.transcribe_gate.set()
    reg.cap.shutdown()


def _recorded(cap, seconds=0.45):
    """Wait until the capture holds `seconds` of recorded AUDIO (its frame count), not of
    wall-clock time: on a slow runner far fewer fake frames arrive per second, and a fixed
    sleep can leave less than the 0.3 s minimum, which is correctly rejected as "No audio".
    """
    import time

    _until(cap, "listening")
    t0 = time.time()
    while cap.elapsed < seconds and time.time() - t0 < 10:
        time.sleep(0.005)
    assert cap.elapsed >= seconds, f"only {cap.elapsed:.2f} s of audio was recorded"


def test_discard_then_immediate_start_never_opens_a_second_microphone(gated):
    cap = gated.cap
    assert cap.start()[0] == 202
    _until(cap, "listening")
    gated.release.clear()                           # the mic will be slow to let go
    st = cap.discard()[1]
    assert st["state"] == "stopping" and st["transcript"] == ""
    for _ in range(20):                             # hammer Start during cleanup
        status, out = cap.start()
        assert status == 409 and "stopping" in out["error"].lower()
    assert cap.status()["state"] == "stopping"
    assert gated.mics == 1 and gated.opened == 1
    gated.release.set()                             # now the mic is released
    _until(cap, "idle")
    assert gated.mics == 0
    assert cap.start()[0] == 202                    # and Start works again
    _until(cap, "listening")
    assert gated.peak_mics == 1 and gated.opened == 2


def test_start_is_refused_while_a_worker_is_alive_even_if_state_looks_idle(gated):
    cap = gated.cap
    cap.start()
    _until(cap, "listening")
    gated.release.clear()
    cap.discard()
    with cap._lock:
        cap.state = "idle"                          # simulate a wrong public state
    assert cap.start()[0] == 409
    assert gated.opened == 1 and gated.peak_mics == 1
    gated.release.set()
    import time
    t0 = time.time()
    while cap._worker_active and time.time() - t0 < 5:
        time.sleep(0.01)
    assert cap.start()[0] == 202


@pytest.mark.parametrize("outcome", ["text", "error"])
def test_discarded_transcription_never_publishes_and_never_overlaps(gated, outcome):
    if outcome == "error":
        gated.heard = RuntimeError("/Users/me/model.bin broke")
        gated.cap._factory = lambda: _GatedMic(gated, gated.heard)
    cap = gated.cap
    cap.start()
    _recorded(cap)
    gated.transcribe_gate.clear()                   # transcription will block
    cap.stop()
    _until(cap, "transcribing")
    st = cap.discard()[1]
    assert st["state"] == "stopping" and st["transcript"] == "" and st["confidence"] is None
    assert cap.start()[0] == 409                    # no second transcription can begin
    assert gated.tx == 1
    gated.transcribe_gate.set()                     # the old transcription now returns...
    _until(cap, "idle")
    st = cap.status()
    assert st["state"] == "idle" and st["transcript"] == "" and st["message"] == ""
    assert st["confidence"] is None                 # ...and its result was never shown
    assert cap.start()[0] == 202
    _recorded(cap)
    cap.stop()
    assert _settle(cap)["state"] in ("transcript_ready", "failed")
    assert gated.peak_tx == 1 and gated.peak_mics == 1


def test_discarding_a_finished_transcript_is_still_immediate(gated):
    cap = gated.cap
    cap.start()
    _recorded(cap)
    cap.stop()
    assert _settle(cap)["state"] == "transcript_ready"
    st = cap.discard()[1]
    assert st["state"] == "idle" and st["transcript"] == ""
    assert cap.start()[0] == 202


def test_shutdown_waits_for_the_microphone_release(gated):
    import threading

    cap = gated.cap
    cap.start()
    _until(cap, "listening")
    gated.release.clear()
    threading.Timer(0.2, gated.release.set).start()   # released while shutdown waits
    cap.shutdown()
    assert gated.mics == 0 and cap.status()["state"] == "idle"
