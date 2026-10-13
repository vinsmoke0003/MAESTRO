"""Spoken replies for the workspace UI: MAESTRO reads its own results aloud.

Reuses the terminal voice path's output: `SystemMouth` (the OS voice: `say`,
Windows SAPI, or espeak) and its `speakable()` clean-up, which shortens text
and turns paths into a couple of words.

* Off by default; the user switches it on from the Voice page.
* It speaks only text MAESTRO itself produced (a result summary, a question, a
  refusal, a one-line plan summary). The page cannot make it say arbitrary text,
  and nothing here plans, approves or runs anything.
* Speaking happens on one background thread, so the server never waits on it.
  If a new reply arrives while an older one is still waiting, the older one is
  dropped: only the latest result is worth hearing. "Stop" drops anything
  waiting; a sentence already being spoken finishes (the OS voices cannot be cut
  off mid-sentence, and replies are kept short).
"""

from __future__ import annotations

import threading

TEST_SENTENCE = "This is how MAESTRO will read your results aloud."


class Speaker:
    """Thread-safe, opt-in text-to-speech for the workspace."""

    def __init__(self, mouth_factory=None, availability=None):
        """`mouth_factory()` returns something with `.say(text)` (default: SystemMouth);
        `availability()` says whether the OS voice exists (default: the speech backend).
        Both are injectable so tests never use a real voice.
        """
        self._lock = threading.Condition()
        self._mouth_factory = mouth_factory or self._system_mouth
        self._availability = availability or self._system_available
        self._mouth = None
        self.enabled = False
        self._pending: str | None = None
        self._speaking = False
        self._failed = False
        self._closing = False
        self._thread: threading.Thread | None = None

    # ---- defaults ------------------------------------------------------------

    @staticmethod
    def _system_mouth():
        """The OS voice the terminal agent uses."""
        from maestro.voice.mouth import SystemMouth

        return SystemMouth()

    @staticmethod
    def _system_available() -> bool:
        """True if this machine has a speech engine (checked without speaking)."""
        try:
            from maestro.executor.platform import backend

            return bool(backend("speech").available())
        except Exception:  # noqa: BLE001
            return False

    # ---- state -----------------------------------------------------------------

    def status(self) -> dict:
        """Whether spoken replies are on, possible on this machine, and speaking right now."""
        available = self._availability() and not self._failed
        with self._lock:
            return {"enabled": self.enabled, "available": available,
                    "speaking": self._speaking or self._pending is not None}

    def set_enabled(self, on: bool) -> tuple[int, dict]:
        """Switch spoken replies on or off. Off also drops anything waiting to be spoken."""
        if on and not (self._availability() and not self._failed):
            return 409, {"error": "No system voice was found on this computer."}
        with self._lock:
            self.enabled = bool(on)
            if not on:
                self._pending = None
        return 200, self.status()

    def stop(self) -> tuple[int, dict]:
        """Drop anything waiting to be spoken (a sentence already started finishes)."""
        with self._lock:
            self._pending = None
        return 200, self.status()

    def test(self) -> tuple[int, dict]:
        """Say a fixed sample sentence, so the user can hear the voice before relying on it."""
        if not self.enabled:
            return 409, {"error": "Turn on spoken replies first."}
        self.say(TEST_SENTENCE)
        return 200, self.status()

    # ---- speaking ------------------------------------------------------------------

    def say(self, text: str | None) -> bool:
        """Queue MAESTRO's own reply to be spoken, if spoken replies are on. Never blocks."""
        from maestro.voice.mouth import speakable

        if not text:
            return False
        line = speakable(str(text))
        with self._lock:
            if not self.enabled or self._closing or not line:
                return False
            self._pending = line          # the latest reply replaces one still waiting
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="maestro-speech",
                                                daemon=True)
                self._thread.start()
            self._lock.notify()
        return True

    def _run(self) -> None:
        """Speak waiting replies one at a time until shut down."""
        while True:
            with self._lock:
                while self._pending is None and not self._closing:
                    self._lock.wait(1.0)
                if self._closing:
                    return
                line, self._pending = self._pending, None
                self._speaking = True
            try:
                if self._mouth is None:
                    self._mouth = self._mouth_factory()
                self._mouth.say(line)
                if getattr(self._mouth, "available", True) is False:
                    self._failed = True           # the OS voice stopped working
            except Exception:  # noqa: BLE001 - a broken voice must never break the server
                self._failed = True
            finally:
                with self._lock:
                    self._speaking = False
                    if self._failed:
                        self.enabled = False
                        self._pending = None

    def shutdown(self, wait_s: float = 2.0) -> None:
        """Server is closing: drop anything waiting and let the speech thread end."""
        with self._lock:
            self._closing = True
            self._pending = None
            self._lock.notify_all()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(wait_s)


def spoken_summary(turn) -> str:
    """The sentence to speak for a turn: the result, the question, or the refusal. A plan that
    is waiting for approval gets one line saying so; its details stay on screen.
    """
    status = getattr(turn, "status", "")
    if status == "completed":
        from maestro.results import present

        # The outcome first, then the answer, exactly as the terminal voice agent says it
        # ("Done, 2 steps completed. I found 2 files: ..."), so the answer is never mistaken
        # for the whole of what happened.
        answer = present(turn).spoken
        message = turn.message or "Done."
        if answer and message.endswith(" found."):
            # "Done — 1 step completed, 204 files found." + "I found 204 files…" would say the
            # count twice; keep it once, in the answer.
            message = message.rsplit(",", 1)[0] + "."
        return f"{message} {answer}" if answer else message
    if status == "previewed":
        plan = getattr(turn, "plan", None)
        n = len(plan.actions) if plan else 0
        return (f"The plan is ready: {n} step{'s' if n != 1 else ''}, risk {turn.risk}. "
                "Check it on screen, then approve it or deny it.")
    return getattr(turn, "message", "") or ""


__all__ = ["Speaker", "spoken_summary", "TEST_SENTENCE"]
