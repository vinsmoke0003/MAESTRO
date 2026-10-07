"""Push-to-talk capture for the workspace UI: one recording at a time, text out.

The Voice page's Start / Stop buttons drive this. It reuses the terminal voice
path's pieces instead of a second recogniser: `MicEars.frames()` opens the
microphone (with its startup watchdog), `vad.rms` measures the level, and
`WhisperTranscriber` turns the audio into text with the same confidence score
`maestro voice` uses.

What it promises:

* the microphone opens only after an explicit start, and is released on stop,
  discard, the time limit, any failure, and server shutdown;
* audio lives only in memory, only until it is transcribed; it is never written
  to disk, never sent anywhere, and never returned;
* the result is TEXT for the user to read and edit. Nothing here plans or runs
  anything: the page copies the text into the Assistant box and the user still
  previews and approves it through the normal consent workflow.
"""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import PurePath

STATES = ("unavailable", "idle", "starting", "listening", "transcribing", "stopping",
          "transcript_ready", "failed")
# "stopping": Discard was pressed while the worker was still recording or
# transcribing; it stays busy until that worker has released the microphone and
# its transcription (if any) has returned. Only then does the state become idle.
BUSY = ("starting", "listening", "transcribing", "stopping")

MAX_SECONDS = 60
LOW_CONFIDENCE = 0.45      # the same bar VoiceAgent uses before acting on speech
MIN_AUDIO_S = 0.3          # anything shorter is a click, not a recording

MESSAGES = {
    "deps": 'Voice input needs the voice extras: pip install -e ".[voice]"',
    "no_mic": "No microphone could be opened. Check that one is connected and selected "
              "as the input device.",
    "blocked": "The microphone did not start. Allow microphone access for the app running "
               "MAESTRO (System Settings > Privacy & Security > Microphone), then try again.",
    "empty": "No audio was recorded. Try again and speak after pressing Start.",
    "no_speech": "No speech was detected. Try again, a little closer to the microphone.",
    "stt": "Speech recognition failed. The first use loads the speech model, which may need "
           "to download it once.",
    "timeout": f"Recording stopped at the {MAX_SECONDS}-second limit.",
    "low": "Low confidence: check the text carefully, edit it, or discard it.",
}


def _missing_deps() -> list[str]:
    """Voice libraries that are not installed (checked without importing them)."""
    names = {"numpy": "numpy", "sounddevice": "sounddevice", "faster_whisper": "faster-whisper"}
    missing = []
    for module, label in names.items():
        try:
            if importlib.util.find_spec(module) is None:
                missing.append(label)
        except (ImportError, ValueError):
            missing.append(label)
    return missing


def _classify(error: BaseException) -> str:
    """Map a microphone or model failure to a fixed message key (no details pass through)."""
    text = str(error).lower()
    if "needs sounddevice" in text or isinstance(error, ImportError):
        return "deps"
    if "did not start within" in text:
        return "blocked"
    return "no_mic"


class VoiceCapture:
    """Thread-safe state machine: idle -> starting -> listening -> transcribing ->
    transcript_ready | failed, and back to idle on discard. Discarding a capture that is
    still running goes through "stopping" until its worker has fully exited.

    At most one worker thread exists at a time: `_worker_active` is set when a worker is
    started and cleared, under the lock, as the very last thing that worker does.
    """

    def __init__(self, ears_factory=None, *, max_seconds: float = MAX_SECONDS,
                 low_confidence: float = LOW_CONFIDENCE):
        """`ears_factory()` returns a MicEars (tests pass a fake). One transcriber is shared
        across recordings so the speech model loads once.
        """
        self._lock = threading.Lock()
        self._factory = ears_factory or self._default_ears
        self._transcriber = None
        self.max_seconds = max_seconds
        self.low_confidence = low_confidence
        self._reset("idle")
        self._run_id = 0
        self._stop = threading.Event()
        self._abort = False
        self._thread: threading.Thread | None = None
        self._worker_active = False
        self._device: str | None = None

    # ---- state ------------------------------------------------------------

    def _reset(self, state: str, message: str = "") -> None:
        """Clear the result fields and set a state (caller holds the lock or is __init__)."""
        self.state = state
        self.message = message
        self.transcript = ""
        self.confidence = None
        self.level = 0.0
        self.started_at = None
        self.elapsed = 0.0

    def _default_ears(self):
        """The real microphone, sharing one WhisperTranscriber."""
        from maestro.voice.ears import MicEars, WhisperTranscriber

        if self._transcriber is None:
            self._transcriber = WhisperTranscriber()
        return MicEars(transcriber=self._transcriber)

    def _model_info(self) -> tuple[str, bool]:
        """The configured Whisper model's short name, and whether it is loaded yet."""
        import os

        from maestro.voice.ears import DEFAULT_STT_MODEL

        t = self._transcriber
        name = t.model_name if t is not None else os.environ.get("MAESTRO_STT_MODEL",
                                                                 DEFAULT_STT_MODEL)
        return PurePath(str(name)).name, bool(t is not None and t._model is not None)

    def _describe_device(self) -> str | None:
        """The default input device's name, read from the device list (nothing is recorded)."""
        try:
            from maestro.voice.ears import MicEars

            return MicEars._sd().query_devices(None, kind="input").get("name") or None
        except Exception:
            return None

    def status(self) -> dict:
        """Safe snapshot for the page: state, level, elapsed time, transcript and confidence.
        Never audio, never paths, never error details.
        """
        missing = _missing_deps()
        with self._lock:
            state = self.state
            if missing and state not in BUSY:
                state = "unavailable"     # whatever the last result was, nothing can record
            busy = state in BUSY
            device = self._device
        if device is None and not missing and not busy:
            device = self._describe_device() or ""     # "" = looked up, name unknown
            with self._lock:
                self._device = device
        model, loaded = self._model_info() if not missing else (None, False)
        with self._lock:
            elapsed = (time.monotonic() - self.started_at) if (
                self.started_at and self.state == "listening") else self.elapsed
            conf = self.confidence
            return {
                "state": state,
                "message": MESSAGES["deps"] if state == "unavailable" else self.message,
                "available": not missing,
                "missing": missing,
                "device": device,
                "model": model,
                "model_loaded": loaded,
                "level": round(self.level, 4),
                "level_pct": min(100, round(self.level * 800)),
                "elapsed_s": round(elapsed, 1),
                "max_s": self.max_seconds,
                "transcript": self.transcript if state == "transcript_ready" else "",
                "confidence": None if conf is None else round(conf, 2),
                "low_confidence": conf is not None and conf < self.low_confidence,
            }

    # ---- actions ------------------------------------------------------------

    def start(self) -> tuple[int, dict]:
        """Open the microphone and start recording, if nothing else is. Returns at once."""
        if _missing_deps():
            return 409, {"error": MESSAGES["deps"]}
        # A worker that has finished its work may still be returning; give it a moment
        # (outside the lock) so the check below can be strict.
        prev = self._thread
        if prev is not None and not self._worker_active and prev.is_alive():
            prev.join(0.5)
        with self._lock:
            if self.state in BUSY:
                return 409, {"error": "A recording is already in progress."
                             if self.state != "stopping" else
                             "Still stopping the previous recording. Try again in a moment."}
            if self._worker_active or (self._thread is not None and self._thread.is_alive()):
                # Never two workers, even if the public state were somehow wrong.
                return 409, {"error": "Still stopping the previous recording. "
                                      "Try again in a moment."}
            self._reset("starting")
            self._run_id += 1
            run_id = self._run_id
            self._stop = threading.Event()
            self._abort = False
            stop = self._stop
            self._worker_active = True
            self._thread = threading.Thread(target=self._run, args=(run_id, stop),
                                            name="voice-capture", daemon=True)
            self._thread.start()
        return 202, self.status()

    def stop(self) -> tuple[int, dict]:
        """Stop recording and transcribe what was heard."""
        with self._lock:
            if self.state not in ("starting", "listening"):
                return 409, {"error": "Nothing is being recorded."}
            self._stop.set()
        return 202, self.status()

    def discard(self) -> tuple[int, dict]:
        """Throw away the recording or transcript.

        A finished result is cleared straight back to idle. A capture that is still
        recording or transcribing is cancelled and its transcript hidden at once, but the
        state stays "stopping" until the worker has closed the microphone and any
        transcription has returned; the worker itself then sets idle. Nothing it produces
        after this point is ever published.
        """
        with self._lock:
            self._abort = True
            self._stop.set()
            self._run_id += 1            # the running worker can no longer publish anything
            still_running = self._worker_active and self.state not in ("transcript_ready",
                                                                        "failed")
            self._reset("stopping" if still_running else "idle")
        return 200, self.status()

    def shutdown(self, wait_s: float = 3.0) -> None:
        """Server is closing: cancel any capture and wait (without the lock) for its worker to
        release the microphone.
        """
        self.discard()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(wait_s)

    # ---- the capture thread ---------------------------------------------------

    def _set(self, run_id: int, **fields) -> bool:
        """Update fields only if this capture is still the current one."""
        with self._lock:
            if run_id != self._run_id:
                return False
            for k, v in fields.items():
                setattr(self, k, v)
            return True

    def _run(self, run_id: int, stop: threading.Event) -> None:
        """Worker thread: capture, then mark this worker finished. A discard that was waiting
        for it ("stopping") becomes idle only here, after everything has been released.
        """
        try:
            self._capture(run_id, stop)
        finally:
            with self._lock:
                self._worker_active = False
                if self.state == "stopping":
                    self._reset("idle")

    def _capture(self, run_id: int, stop: threading.Event) -> None:
        """Record until Stop, the time limit, or an abort; then transcribe in memory."""
        from maestro.voice.vad import FRAME_MS, rms

        chunks: list = []
        timed_out = False
        frames = None
        t0 = None
        try:
            ears = self._factory()
            frames = ears.frames()
            for frame in frames:
                if stop.is_set():
                    break
                if t0 is not None and time.monotonic() - t0 >= self.max_seconds:
                    timed_out = True     # wall clock: holds even if the stream stalls
                    break
                if frame is None:
                    continue
                if t0 is None:
                    t0 = time.monotonic()
                    if not self._set(run_id, state="listening", started_at=t0):
                        break
                chunks.append(frame)
                elapsed = len(chunks) * FRAME_MS / 1000
                self._set(run_id, level=float(rms(frame)), elapsed=elapsed)
                if elapsed >= self.max_seconds:
                    timed_out = True
                    break
        except Exception as e:  # noqa: BLE001 - mapped to a fixed message
            self._set(run_id, state="failed", message=MESSAGES[_classify(e)], level=0.0)
            return
        finally:
            if frames is not None:
                frames.close()           # releases the microphone (MicEars.frames' finally)

        with self._lock:
            aborted = self._abort or run_id != self._run_id
        if aborted:
            return
        if len(chunks) * FRAME_MS / 1000 < MIN_AUDIO_S:
            self._set(run_id, state="failed", message=MESSAGES["empty"], level=0.0)
            return

        if not self._set(run_id, state="transcribing", level=0.0,
                         elapsed=len(chunks) * FRAME_MS / 1000):
            return
        try:
            import numpy as np

            audio = np.concatenate(chunks).astype("float32")
            chunks.clear()
            heard = ears.transcriber.transcribe(audio)
            del audio
        except Exception:  # noqa: BLE001
            self._set(run_id, state="failed", message=MESSAGES["stt"])
            return
        if heard.empty:
            self._set(run_id, state="failed", message=MESSAGES["no_speech"])
            return
        conf = float(heard.confidence)
        notes = [MESSAGES["timeout"]] if timed_out else []
        if conf < self.low_confidence:
            notes.append(MESSAGES["low"])
        self._set(run_id, state="transcript_ready", transcript=heard.text.strip(),
                  confidence=conf, message=" ".join(notes))
