"""Ears: how the voice agent hears one utterance and turns it into text.

Everything the agent consumes goes through one tiny interface, `Ears.listen()`,
which returns a `Heard` (text + how sure the recogniser was). Three
implementations:

* `MicEars`      — microphone -> Endpointer -> local Whisper. The real one.
* `KeyboardEars` — typed lines. `maestro voice --text`, and a fallback when
                   there is no microphone.
* `ScriptedEars` — a fixed list of utterances, for tests.

Speech recognition is LOCAL (faster-whisper, CTranslate2 on CPU). No audio
leaves the machine and nothing costs money, which is the same rule the planner
follows with Ollama. The model is downloaded once on first use, then cached.

The microphone is opened for one utterance at a time and closed before MAESTRO
speaks, so the agent cannot hear — and act on — its own voice.
"""

from __future__ import annotations

import math
import os
import queue
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

DEFAULT_STT_MODEL = "base.en"

# Biases Whisper toward desktop vocabulary. Without it "PDFs" is heard as
# "PDSS" and "Downloads" as "down loads". It is a hint, not a constraint:
# anything the user actually says is still transcribed as said.
VOCABULARY_HINT = (
    "Commands for a desktop assistant called Maestro: Maestro, move the PDFs from "
    "Downloads to Documents, find screenshots on the Desktop, zip files, open Chrome, "
    "how much disk space, battery, volume, draft an email, Recycle Bin, folder."
)


class VoiceUnavailable(RuntimeError):
    """A voice dependency (microphone library, speech model) is missing."""


@dataclass(frozen=True)
class Heard:
    text: str
    confidence: float = 1.0      # 0..1; typed input is certain
    source: str = "voice"        # voice | keyboard | script

    @property
    def empty(self) -> bool:
        return not self.text.strip()


class Ears(Protocol):
    def listen(self, timeout_s: float | None = None) -> Heard | None:
        """One utterance, or None when the input is closed (EOF / Ctrl-D)."""
        ...


# --------------------------------------------------------------------------- #
# test + keyboard ears
# --------------------------------------------------------------------------- #


class ScriptedEars:
    def __init__(self, lines: Iterable[str | Heard]):
        self._lines = [x if isinstance(x, Heard) else Heard(x, 1.0, "script")
                       for x in lines]

    def listen(self, timeout_s: float | None = None) -> Heard | None:
        return self._lines.pop(0) if self._lines else None


class KeyboardEars:
    def __init__(self, prompt: str = "you> "):
        self.prompt = prompt

    def listen(self, timeout_s: float | None = None) -> Heard | None:
        try:
            return Heard(input(self.prompt), 1.0, "keyboard")
        except (EOFError, KeyboardInterrupt):
            return None


# --------------------------------------------------------------------------- #
# speech to text
# --------------------------------------------------------------------------- #


class WhisperTranscriber:
    """faster-whisper, loaded lazily so `import maestro` never pays for it."""

    def __init__(self, model: str | None = None, *, compute_type: str = "int8"):
        self.model_name = model or os.environ.get("MAESTRO_STT_MODEL", DEFAULT_STT_MODEL)
        self.compute_type = compute_type
        self._model = None

    def load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise VoiceUnavailable(
                    "speech recognition needs faster-whisper: pip install -e \".[voice]\""
                ) from e
            self._model = WhisperModel(self.model_name, device="cpu",
                                       compute_type=self.compute_type)
        return self._model

    def transcribe(self, audio) -> Heard:
        model = self.load()
        segments, _info = model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
            condition_on_previous_text=False, initial_prompt=VOCABULARY_HINT,
        )
        segs = list(segments)
        text = " ".join(s.text.strip() for s in segs).strip()
        if not segs:
            return Heard("", 0.0)
        # avg_logprob is per-token log probability; exp() gives a rough 0..1
        # confidence. A segment the model thinks is not speech drags it down.
        conf = sum(math.exp(s.avg_logprob) * (1.0 - s.no_speech_prob) for s in segs) / len(segs)
        return Heard(text, max(0.0, min(1.0, conf)), "voice")


# --------------------------------------------------------------------------- #
# the microphone
# --------------------------------------------------------------------------- #


class MicEars:
    """Microphone -> Endpointer -> Whisper, with live feedback.

    Two optional callbacks make the listening visible, as the web page's
    "Listening..." text and animated orb did:

    * `on_level(level, threshold, speaking)` — about 6 times a second, for a
      level meter, so a muted or wrong microphone is obvious immediately;
    * `on_partial(text)` — live captions: while you are still speaking, the
      audio so far is re-transcribed every `partial_every_s` seconds on a
      background thread, so your words appear as you say them. The final,
      authoritative transcript is always made from the complete utterance.
    """

    def __init__(self, transcriber: WhisperTranscriber | None = None,
                 endpointer=None, *, device: int | str | None = None, on_state=None,
                 on_level=None, on_partial=None, partial_every_s: float = 0.8,
                 push_to_talk: bool = False, wait_key=None):
        from maestro.voice.vad import Endpointer  # numpy: only needed for a real mic

        self.transcriber = transcriber or WhisperTranscriber()
        self.endpointer = endpointer or Endpointer()
        self.device = device
        self.on_state = on_state or (lambda _state: None)
        self.on_level = on_level
        self.on_partial = on_partial
        self.partial_every_s = partial_every_s
        # Push-to-talk: the user presses Enter to start and again to finish,
        # instead of the endpointer deciding when speech began and ended.
        self.push_to_talk = push_to_talk
        self.wait_key = wait_key or (lambda prompt: input(prompt))
        self._worker = None

    @staticmethod
    def _sd():
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:
            raise VoiceUnavailable(
                "microphone access needs sounddevice: pip install -e \".[voice]\""
            ) from e
        return sd

    def check(self) -> str:
        """Raise VoiceUnavailable unless an input device exists; describe it."""
        sd = self._sd()
        try:
            info = sd.query_devices(self.device, kind="input")
        except Exception as e:
            raise VoiceUnavailable(f"no microphone found: {e}") from e
        return str(info.get("name", "default input"))

    def warm_up(self) -> None:
        """Load the speech model now, so the first command is not slow."""
        self.transcriber.load()

    def _pool(self):
        # One worker: Whisper runs one job at a time, and the final transcript
        # queues behind any caption still in flight instead of racing it.
        if self._worker is None:
            from concurrent.futures import ThreadPoolExecutor

            self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        return self._worker

    START_TIMEOUT_S = 5.0

    MIC_BLOCKED = (
        "the microphone did not start within {t:.0f} s. On macOS this means the app "
        "running this terminal is not allowed to use the microphone. Run MAESTRO from "
        "the macOS Terminal app instead (it asks for permission), or enable it in "
        "System Settings > Privacy & Security > Microphone."
    )

    def frames(self):
        """Yield 30 ms float32 frames from the microphone until the caller stops.

        The stream is opened on a helper thread. When macOS blocks microphone
        access it does not raise — opening the stream simply never returns —
        so without a watchdog the agent would sit at "listening" forever with
        no explanation. If no audio arrives within START_TIMEOUT_S, raise
        VoiceUnavailable with the fix instead.
        """
        import threading

        from maestro.voice.vad import FRAME_SAMPLES, SAMPLE_RATE

        sd = self._sd()
        q: queue.Queue = queue.Queue()
        stop = threading.Event()
        failed: list[BaseException] = []

        def callback(indata, _n, _time, status):  # runs on the audio thread
            q.put(indata[:, 0].copy())

        def run() -> None:
            try:
                with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    blocksize=FRAME_SAMPLES, device=self.device,
                                    callback=callback):
                    stop.wait()
            except BaseException as e:  # surfaced to the caller below
                failed.append(e)
                q.put(None)

        threading.Thread(target=run, name="mic", daemon=True).start()
        try:
            try:
                first = q.get(timeout=self.START_TIMEOUT_S)
            except queue.Empty:
                raise VoiceUnavailable(
                    self.MIC_BLOCKED.format(t=self.START_TIMEOUT_S)) from None
            if first is None:
                raise VoiceUnavailable(f"could not open the microphone: {failed[0]}")
            yield first
            while True:
                try:
                    yield q.get(timeout=0.5)
                except queue.Empty:
                    yield None          # lets the caller count time and time out
        finally:
            stop.set()

    def record_push_to_talk(self):
        """Enter to start, Enter to stop. Returns the samples, or None on EOF."""
        import threading

        import numpy as np

        from maestro.voice.vad import FRAME_MS, rms

        try:
            self.wait_key("  press Enter, then speak ")
        except (EOFError, KeyboardInterrupt):
            return None
        done = threading.Event()

        def wait_for_enter() -> None:
            try:
                self.wait_key("")
            except (EOFError, KeyboardInterrupt):
                pass
            done.set()

        self.on_state("hearing")
        print("  recording... press Enter when you have finished", flush=True)
        threading.Thread(target=wait_for_enter, daemon=True).start()
        chunks: list = []
        n = 0
        last_partial_at = 0
        pending = None
        for frame in self.frames():
            if done.is_set():
                break
            if frame is None:
                continue
            chunks.append(frame)
            n += 1
            if self.on_level and n % 5 == 0:
                self.on_level(rms(frame), 1.0, True)
            due = (n - last_partial_at) * FRAME_MS >= self.partial_every_s * 1000
            if self.on_partial and due and (pending is None or pending.done()):
                last_partial_at = n
                pending = self._pool().submit(self._caption, np.concatenate(chunks))
        if pending is not None:
            pending.cancel()              # a stale caption is not worth waiting for
        return np.concatenate(chunks).astype("float32") if chunks else None

    def record(self, timeout_s: float | None = None):
        """Block until one utterance is complete; return its samples or None."""
        from maestro.voice.vad import FRAME_MS

        if self.push_to_talk:
            return self.record_push_to_talk()

        ep = self.endpointer
        ep.reset()
        waited = 0.0
        n = 0
        last_partial_at = 0
        pending = None
        self.on_state("listening")
        for frame in self.frames():
            if frame is None:
                waited += 0.5
                if timeout_s is not None and waited >= timeout_s:
                    return None
                continue
            n += 1
            was_speaking = ep.in_speech
            utterance = ep.feed(frame)
            if ep.in_speech and not was_speaking:
                self.on_state("hearing")
                last_partial_at = n
            if self.on_level and n % 5 == 0:
                self.on_level(ep.level, ep.start_level, ep.in_speech)
            if utterance is not None:
                return utterance
            if ep.in_speech and self.on_partial:
                due = (n - last_partial_at) * FRAME_MS >= self.partial_every_s * 1000
                if due and (pending is None or pending.done()):
                    audio = ep.current()
                    if audio is not None:
                        last_partial_at = n
                        pending = self._pool().submit(self._caption, audio)
            if not ep.in_speech:
                waited += FRAME_MS / 1000
                if timeout_s is not None and waited >= timeout_s:
                    return None
        return None

    def _caption(self, audio) -> None:
        try:
            text = self.transcriber.transcribe(audio).text
        except Exception:
            return
        if text and self.on_partial:
            self.on_partial(text)

    def listen(self, timeout_s: float | None = None) -> Heard | None:
        audio = self.record(timeout_s)
        if audio is None:
            return Heard("", 0.0)
        self.on_state("transcribing")
        if self._worker is not None:
            return self._worker.submit(self.transcriber.transcribe, audio).result()
        return self.transcriber.transcribe(audio)
