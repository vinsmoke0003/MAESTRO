"""'Hey Maestro' (maestro.voice.wake), with a fake microphone and a fake transcriber.

The fake microphone "speaks" scripted sentences as synthetic audio, and the REAL
Endpointer cuts them into utterances, so sentence detection is exercised for
real. The fake transcriber returns the scripted words. No real microphone,
speech model or network is used.

What these pin: off by default; only sentences addressed to MAESTRO are used and
everything else is dropped without a trace; a bare "Hey Maestro" arms it
briefly; unclear speech is not acted on; it never listens to its own voice; one
request at a time; the microphone is always released; failures say something
fixed and safe.
"""

from __future__ import annotations

import json
import threading
import time
import types

import pytest

np = pytest.importorskip("numpy")

from maestro.voice import Heard  # noqa: E402
from maestro.voice import capture as capture_mod  # noqa: E402
from maestro.voice.vad import FRAME_SAMPLES, SAMPLE_RATE  # noqa: E402
from maestro.voice.wake import MESSAGES, WakeListener  # noqa: E402

PRIVATE = "my bank pin is 4321"           # someone else's sentence: must never surface


class ScriptedMic:
    """Says each scripted (text, confidence) as ~1 s of 'speech' between silences."""

    def __init__(self, reg, script, fail=None, stt_fail=False):
        self.reg, self.script, self.fail, self.stt_fail = reg, list(script), fail, stt_fail
        self.transcriber = self
        self._rng = np.random.default_rng(0)

    def _frames(self, seconds, level):
        n = int(seconds * SAMPLE_RATE / FRAME_SAMPLES)
        return [(self._rng.standard_normal(FRAME_SAMPLES) * level).astype("float32")
                for _ in range(n)]

    def frames(self):
        self.reg.opened += 1
        try:
            if self.fail:
                raise self.fail
            for f in self._frames(0.6, 0.002):          # room tone: calibration
                yield f
            for _ in range(len(self.script)):
                self.reg.gate.wait(5)
                for f in self._frames(1.0, 0.08) + self._frames(1.2, 0.002):
                    yield f
                self.reg.spoken += 1
            while True:                                 # quiet room until stopped
                time.sleep(0.005)
                yield None
        finally:
            self.reg.closed += 1
            # What the page would have shown at the moment the microphone was released.
            listener = getattr(self.reg, "listener", None)
            self.reg.state_at_release = listener.state if listener else None

    def transcribe(self, audio):
        if self.stt_fail:
            raise RuntimeError("/Users/me/.cache/whisper/model.bin is corrupt")
        text, conf = self.script[self.reg.transcribed]
        self.reg.transcribed += 1
        return Heard(text, conf, "voice")


@pytest.fixture
def rig(monkeypatch):
    from maestro.voice import ears

    def forbidden(*a, **k):
        raise AssertionError("real microphone / speech model used in a test")

    monkeypatch.setattr(ears.MicEars, "frames", forbidden)
    monkeypatch.setattr(ears.WhisperTranscriber, "load", forbidden)
    monkeypatch.setattr(capture_mod, "_missing_deps", lambda: [])
    import maestro.voice.wake as wake_mod
    monkeypatch.setattr(wake_mod, "_missing_deps", lambda: [])

    reg = types.SimpleNamespace(opened=0, closed=0, transcribed=0, spoken=0,
                                gate=threading.Event(), delivered=[], said=[],
                                speaking=False, deliver_gate=threading.Event())
    reg.gate.set()
    reg.deliver_gate.set()

    def deliver(text):
        reg.deliver_gate.wait(5)
        reg.delivered.append(text)
        return {"status": "previewed", "instruction": text, "actions": [{"verb": "fs.glob"}]}

    def make(script, **kw):
        mic_kw = {k: kw.pop(k) for k in ("fail", "stt_fail") if k in kw}
        w = WakeListener(deliver, ears_factory=lambda: ScriptedMic(reg, script, **mic_kw),
                         is_speaking=lambda: reg.speaking, say=reg.said.append, **kw)
        reg.listener = w
        return w

    reg.make = make
    yield reg
    if getattr(reg, "listener", None):
        reg.gate.set()
        reg.deliver_gate.set()
        reg.listener.shutdown()


def _until(pred, timeout=10.0):
    t0 = time.time()
    while not pred() and time.time() - t0 < timeout:
        time.sleep(0.01)
    assert pred()


def _settled(w, reg, n):
    """Wait until n scripted sentences have been spoken and handled."""
    _until(lambda: reg.spoken >= n and reg.transcribed >= n
           and not w.status()["planning"] and w.status()["state"] == "listening")


# ---- switching ----------------------------------------------------------------------


def test_off_by_default_and_status_never_opens_the_microphone(rig):
    w = rig.make([])
    for _ in range(3):
        st = w.status()
    assert st["state"] == "off" and st["enabled"] is False and rig.opened == 0


def test_start_stop_releases_the_microphone(rig):
    w = rig.make([])
    assert w.start()[0] == 202
    _until(lambda: w.status()["state"] == "listening")
    assert w.status()["enabled"] is True and w.start()[0] == 409     # already on
    assert w.stop()[0] == 200
    _until(lambda: w.status()["state"] == "off")
    assert rig.opened == 1 and rig.closed == 1 and not w.active
    assert rig.state_at_release == "stopping"       # released BEFORE it reported "off"


def test_cannot_start_while_push_to_talk_holds_the_microphone(rig):
    w = WakeListener(lambda t: {}, ears_factory=lambda: ScriptedMic(rig, []),
                     can_start=lambda: "Push-to-talk is using the microphone.")
    status, out = w.start()
    assert status == 409 and "Push-to-talk" in out["error"] and rig.opened == 0


def test_shutdown_releases_the_microphone(rig):
    w = rig.make([])
    w.start()
    _until(lambda: w.status()["state"] == "listening")
    w.shutdown()
    assert rig.closed == 1 and w.status()["state"] == "off"
    assert rig.state_at_release == "stopping"


# ---- what it does with what it hears -----------------------------------------------


def test_a_request_after_the_wake_word_is_delivered_as_text(rig):
    w = rig.make([("Hey Maestro, move the pdfs to documents slash invoices", 0.9)])
    w.start()
    _settled(w, rig, 1)
    assert rig.delivered == ["move the pdfs to documents/invoices"]   # spoken path fixed
    ev = w.status()["event"]
    assert ev["id"] == 1 and ev["text"] == "move the pdfs to documents/invoices"
    assert ev["turn"]["status"] == "previewed"


def test_other_peoples_sentences_are_dropped_without_a_trace(rig):
    w = rig.make([(PRIVATE, 0.95), ("what are we having for lunch", 0.9)])
    w.start()
    _settled(w, rig, 2)
    st = w.status()
    assert rig.delivered == [] and st["ignored"] == 2 and st["event"] is None
    text = json.dumps(st).lower()
    assert "4321" not in text and "bank" not in text and "lunch" not in text


def test_a_bare_wake_word_arms_it_for_the_next_sentence(rig):
    w = rig.make([("Hey Maestro", 0.9), ("what time is it", 0.9)])
    rig.gate.clear()
    w.start()
    _until(lambda: w.status()["state"] == "listening")
    rig.gate.set()
    _settled(w, rig, 2)
    assert rig.said == ["Yes?"] and rig.delivered == ["what time is it"]


def test_arming_expires(rig):
    w = rig.make([("Maestro", 0.9), ("what time is it", 0.9)], armed_s=0.0)
    w.start()
    _settled(w, rig, 2)
    assert rig.delivered == [] and w.status()["ignored"] == 1


def test_unclear_speech_is_not_acted_on(rig):
    w = rig.make([("hey maestro, delete the reports", 0.2)])
    w.start()
    _settled(w, rig, 1)
    assert rig.delivered == [] and w.status()["message"] == MESSAGES["unclear"]


def test_it_never_listens_to_its_own_voice(rig):
    rig.speaking = True                             # MAESTRO is talking the whole time
    w = rig.make([("Hey Maestro, delete everything", 0.95)])
    w.start()
    _until(lambda: rig.spoken >= 1)
    time.sleep(0.2)
    assert rig.transcribed == 0 and rig.delivered == []


def test_one_request_at_a_time(rig):
    rig.deliver_gate.clear()                        # the first plan takes a while
    w = rig.make([("Maestro, find my pdfs", 0.9), ("Maestro, what time is it", 0.9)])
    w.start()
    _until(lambda: rig.transcribed >= 2 and w.status()["message"] == MESSAGES["busy"])
    rig.deliver_gate.set()
    _until(lambda: not w.status()["planning"])
    assert rig.delivered == ["find my pdfs"]


# ---- failures -----------------------------------------------------------------------------


@pytest.mark.parametrize("error, expected", [
    ("the microphone did not start within 5 s. On macOS ...", "Allow microphone access"),
    ("could not open the microphone: PortAudioError -9986 /dev/mic", "No microphone"),
])
def test_microphone_failures_are_generic(rig, error, expected):
    from maestro.voice import VoiceUnavailable

    w = rig.make([], fail=VoiceUnavailable(error))
    w.start()
    _until(lambda: w.status()["state"] == "failed")
    st = w.status()
    assert expected in st["message"] and "PortAudio" not in st["message"]
    assert rig.closed == 1


def test_transcription_failure_is_generic_and_releases_the_microphone(rig):
    w = rig.make([("Hey Maestro, hello", 0.9)], stt_fail=True)
    w.start()
    _until(lambda: w.status()["state"] == "failed")
    st = w.status()
    assert "Speech recognition failed" in st["message"] and "/Users" not in st["message"]
    assert rig.closed == 1 and rig.delivered == []


def test_unavailable_without_voice_libraries(rig, monkeypatch):
    import maestro.voice.wake as wake_mod

    monkeypatch.setattr(wake_mod, "_missing_deps", lambda: ["sounddevice"])
    w = rig.make([])
    assert w.status()["state"] == "unavailable"
    assert w.start()[0] == 409 and rig.opened == 0
