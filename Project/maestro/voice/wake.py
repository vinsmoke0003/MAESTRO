"""'Hey Maestro' for the workspace UI: hands-free requests that still need your click.

When switched on, one background thread keeps a single microphone stream open
(`MicEars.frames()`), cuts it into sentences with the same `Endpointer` the
terminal agent uses, and transcribes each one locally with Whisper. Then:

* A sentence that starts with the wake word ("Hey Maestro, find my pdfs") is a
  request. It goes to `deliver(text)`, which in the workspace fills the
  Assistant box and shows the plan PREVIEW. Nothing runs: approval is still a
  click, and high-risk plans still need the typed confirmation.
* "Hey Maestro" on its own arms it for a few seconds ("Yes?"); the next
  sentence is the request.
* Every other sentence is someone else's conversation. It is dropped at once:
  never shown, stored, logged or sent anywhere. Only a counter goes up.
* While MAESTRO is speaking, what the microphone hears is ignored, so it never
  answers itself.

Off by default and never remembered between launches. Audio stays in memory
and only until it is transcribed.
"""

from __future__ import annotations

import threading
import time

from maestro.voice.capture import MESSAGES as CAPTURE_MESSAGES
from maestro.voice.capture import _classify, _missing_deps

WAKE_WORD = "maestro"
ARMED_S = 6.0                 # after a bare "Hey Maestro", how long to wait for the request
LOW_CONFIDENCE = 0.45         # the same bar as the terminal voice agent

BUSY = ("starting", "listening", "hearing", "transcribing", "stopping")

MESSAGES = {
    "listening": 'Listening for "Hey Maestro". Everything else it hears is discarded.',
    "armed": "Yes? Say your request.",
    "unclear": "Sorry, I did not catch that clearly. Say \"Hey Maestro\" and try again.",
    "busy": "Still working on the last request. Try again in a moment.",
    "failed": "Hey Maestro could not handle that request. Try typing it instead.",
}


class WakeListener:
    """Thread-safe, opt-in wake-word listening that delivers requests as text."""

    def __init__(self, deliver, ears_factory=None, *, wake: str = WAKE_WORD,
                 is_speaking=None, can_start=None, say=None,
                 min_confidence: float = LOW_CONFIDENCE, armed_s: float = ARMED_S):
        """`deliver(text)` plans a request and returns the turn as JSON (the workspace's
        `plan`). `is_speaking()` says whether MAESTRO's own voice is talking; `can_start()`
        returns a reason the microphone is busy, or None; `say(text)` speaks a short prompt.
        """
        self._lock = threading.Lock()
        self._deliver = deliver
        self._factory = ears_factory or self._default_ears
        self.wake = wake
        self._is_speaking = is_speaking or (lambda: False)
        self._can_start = can_start or (lambda: None)
        self._say = say or (lambda text: None)
        self.min_confidence = min_confidence
        self.armed_s = armed_s
        self.state = "off"
        self.message = ""
        self.level = 0.0
        self.ignored = 0
        self.event: dict | None = None        # {"id", "text", "turn"} for the page
        self._event_id = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._armed_until = 0.0
        self._planning = False

    @staticmethod
    def _default_ears():
        """The real microphone (tests pass a fake)."""
        from maestro.voice.ears import MicEars

        return MicEars()

    # ---- state ---------------------------------------------------------------

    @property
    def active(self) -> bool:
        """True while listening is on or still shutting down (the microphone may be open)."""
        t = self._thread
        return t is not None and t.is_alive()

    def status(self) -> dict:
        """Safe snapshot: state, level, a fixed message, and the last delivered request.
        Never audio, and never what was heard unless it was addressed to MAESTRO.
        """
        missing = _missing_deps()
        with self._lock:
            state = self.state
            if missing and state not in BUSY:
                state = "unavailable"
            return {
                "state": state,
                "enabled": state in BUSY and state != "stopping",
                "available": not missing,
                "message": CAPTURE_MESSAGES["deps"] if state == "unavailable" else self.message,
                "level_pct": min(100, round(self.level * 800)),
                "ignored": self.ignored,
                "planning": self._planning,
                "event": dict(self.event) if self.event else None,
            }

    # ---- switching -------------------------------------------------------------

    def start(self) -> tuple[int, dict]:
        """Open the microphone and listen for the wake word."""
        if _missing_deps():
            return 409, {"error": CAPTURE_MESSAGES["deps"]}
        busy = self._can_start()
        if busy:
            return 409, {"error": busy}
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return 409, {"error": "Hey Maestro is already on or still stopping."}
            self.state, self.message, self.level = "starting", "", 0.0
            self._armed_until = 0.0
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._stop,),
                                            name="maestro-wake", daemon=True)
            self._thread.start()
        return 202, self.status()

    def stop(self) -> tuple[int, dict]:
        """Stop listening. The microphone is released when the thread finishes (moments)."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self.state, self.message = "off", ""
            else:
                self._stop.set()
                self.state, self.message, self.level = "stopping", "", 0.0
        return 200, self.status()            # status() takes the lock itself

    def shutdown(self, wait_s: float = 3.0) -> None:
        """Server is closing: stop and wait (without the lock) for the microphone release."""
        self.stop()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(wait_s)

    # ---- the listening thread ------------------------------------------------------

    def _set(self, **fields) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self, k, v)

    def _run(self, stop: threading.Event) -> None:
        """Read frames, cut sentences, transcribe, and handle each one."""
        from maestro.voice.vad import Endpointer, rms

        frames = None
        failed = None
        try:
            ears = self._factory()
            frames = ears.frames()
            ep = Endpointer()
            started = False
            for frame in frames:
                if stop.is_set():
                    break
                if frame is None:
                    continue
                if not started:
                    started = True
                    self._set(state="listening", message=MESSAGES["listening"])
                if self._is_speaking():
                    ep.reset()                       # never answer our own voice
                    self._set(level=0.0)
                    continue
                self._set(level=float(rms(frame)),
                          state="hearing" if ep.in_speech else "listening")
                utterance = ep.feed(frame)
                if utterance is None:
                    continue
                self._set(state="transcribing", level=0.0)
                try:
                    heard = ears.transcriber.transcribe(utterance)
                except Exception:  # noqa: BLE001 - the model failed, not the microphone
                    failed = CAPTURE_MESSAGES["stt"]
                    break
                del utterance
                if stop.is_set():
                    break
                self._handle(heard)
                self._set(state="listening")
        except Exception as e:  # noqa: BLE001 - a fixed message, never details
            failed = CAPTURE_MESSAGES[_classify(e)]
        finally:
            if frames is not None:
                frames.close()                       # releases the microphone
            with self._lock:
                if failed:
                    self.state, self.message = "failed", failed
                else:
                    self.state, self.message = "off", ""
                self.level = 0.0

    def _handle(self, heard) -> None:
        """Decide what one transcribed sentence is: a request, a bare wake word, or not ours."""
        from maestro.voice.agent import spoken_to_text, strip_wake

        text = (heard.text or "").strip()
        if not text:
            return
        now = time.monotonic()
        command = strip_wake(text, self.wake)
        if command is None:
            if now < self._armed_until:
                command = text                       # the request after "Hey Maestro"
            else:
                with self._lock:
                    self.ignored += 1                # someone else's conversation: dropped
                return
        elif command == "":
            self._armed_until = now + self.armed_s
            self._set(message=MESSAGES["armed"])
            self._say("Yes?")
            return
        self._armed_until = 0.0
        if float(heard.confidence) < self.min_confidence:
            self._set(message=MESSAGES["unclear"])
            self._say("Sorry, I did not catch that.")
            return
        with self._lock:
            if self._planning:
                self.message = MESSAGES["busy"]
                return
            self._planning = True
        command = spoken_to_text(command)
        threading.Thread(target=self._plan, args=(command,), name="maestro-wake-plan",
                         daemon=True).start()

    def _plan(self, command: str) -> None:
        """Plan one request (preview only) and publish it for the page."""
        try:
            turn = self._deliver(command)
            message = MESSAGES["listening"]
        except Exception:  # noqa: BLE001
            turn, message = {"status": "failed", "message": MESSAGES["failed"]}, MESSAGES["failed"]
        with self._lock:
            self._event_id += 1
            self.event = {"id": self._event_id, "text": command, "turn": turn}
            self._planning = False
            if self.state in ("listening", "hearing", "transcribing"):
                self.message = message


__all__ = ["WakeListener", "WAKE_WORD", "MESSAGES"]
