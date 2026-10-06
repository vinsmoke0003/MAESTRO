"""The voice agent: `maestro voice` — listen, understand, act, answer aloud.

    you speak ─▶ Ears (mic ▸ endpointer ▸ local Whisper) ─▶ text
         ─▶ MaestroPipeline.handle()   ← the SAME lifecycle the CLI, web UI and
                                          benchmark use: NLP, planner, safety
                                          scorer, dry run, consent, audit
         ─▶ Mouth (the OS voice) ─▶ you hear the outcome

Voice is only a new way in and out. It adds no capability and bypasses no
control; everything MAESTRO can do by voice it can already do by typing, and
every refusal, question and consent gate is the one the typed path has.

Voice does add one risk of its own: mishearing (threat T1, misinterpretation).
The controls for it are here:

* every recognised command is printed back before anything happens;
* a low-confidence transcription is never acted on — the agent asks again;
* an R2 plan needs an explicit spoken "yes" after the preview, and anything
  containing a negation ("no", "wait", "don't", "cancel") is a refusal;
* an R3 plan can NEVER be approved by voice. The confirmation token must be
  typed on the keyboard, exactly as in `maestro ask` — `ConsentGate` itself
  rejects any non-typed approval of an R3 plan, so this is enforced below the
  voice layer, not just by it.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from maestro.orchestrator import render_preview
from maestro.pipeline import MaestroPipeline, Turn
from maestro.results import present
from maestro.safety import Approval, ConsentGate, ConsentRequest, token_matches
from maestro.voice.ears import Ears, Heard
from maestro.voice.mouth import Mouth

EXIT_PHRASES = (
    "goodbye", "good bye", "bye", "stop listening", "exit", "quit",
    "shut down", "go to sleep", "that's all", "that is all",
)
YES_WORDS = ("yes", "yeah", "yep", "yup", "approve", "approved", "confirm", "confirmed",
             "go ahead", "do it", "proceed", "sure", "okay", "ok", "affirmative")
NO_WORDS = ("no", "nope", "nah", "don't", "dont", "do not", "cancel", "stop", "wait",
            "not", "never", "abort", "deny", "hold on", "negative")

GREETING = ("MAESTRO is listening. Tell me what to do on this computer. "
            "Say goodbye when you are done.")
WAKE_GREETING = ("MAESTRO is listening. Start each command with {wake}. "
                 "Say goodbye when you are done.")


def parse_yes_no(text: str) -> bool | None:
    """True = approve, False = refuse, None = could not tell.

    Negation wins: "yes, no wait" is a refusal. Default-deny is applied by the
    caller when this stays None.
    """
    t = " " + re.sub(r"[^a-z' ]+", " ", text.lower()) + " "
    if any(f" {w} " in t for w in NO_WORDS):
        return False
    if any(f" {w} " in t for w in YES_WORDS):
        return True
    return None


def spoken_to_text(text: str) -> str:
    """Undo the ways paths come out of speech: 'Documents slash Invoices'."""
    t = re.sub(r"\s+(?:forward\s+)?slash\s+", "/", text, flags=re.I)
    t = re.sub(r"\s+dot\s+(pdf|txt|zip|png|jpg|docx|csv)\b", r".\1", t, flags=re.I)
    return t.strip().rstrip(".")


def is_exit(text: str) -> bool:
    """The whole utterance must be a sign-off. "quit Spotify" is a command for
    app.quit, not a request to end the session, so prefixes do not count."""
    t = re.sub(r"[^a-z' ]+", " ", text.lower())
    t = re.sub(r"\b(maestro|thanks|thank you|please|now|ok|okay)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t in EXIT_PHRASES


def strip_wake(text: str, wake: str) -> str | None:
    """'Hey Maestro, open notes' -> 'open notes'. None if the wake word is absent."""
    pattern = rf"^\s*(?:hey|hi|ok|okay)?[\s,]*{re.escape(wake)}\b[\s,.!:]*"
    m = re.match(pattern, text, flags=re.I)
    return text[m.end():].strip() if m else None


class VoiceAgent:
    """One conversation with MAESTRO, spoken instead of typed."""

    MAX_CONSENT_TRIES = 2
    MAX_REPEATS = 2

    def __init__(
        self,
        ears: Ears,
        mouth: Mouth,
        *,
        pipeline_factory: Callable[[ConsentGate], MaestroPipeline] | None = None,
        wake_word: str | None = None,
        min_confidence: float = 0.45,
        show: Callable[[str], None] = print,
        typed_input: Callable[[str], str] = input,
    ):
        """Set up the voice agent with its ears (input), mouth (speech output), optional wake word,
        and a pipeline whose consent questions are asked by voice.
        """
        self.ears = ears
        self.mouth = mouth
        self.wake_word = wake_word
        self.min_confidence = min_confidence
        self.show = show
        self.typed_input = typed_input
        gate = ConsentGate(ask=self._consent)
        factory = pipeline_factory or (lambda g: MaestroPipeline(gate=g))
        self.pipe = factory(gate)
        self.turns: list[Turn] = []

    # ------------------------------------------------------------ talking --

    def speak(self, text: str) -> None:
        """Print a reply and say it aloud."""
        self.show(f"maestro: {text}")
        self.mouth.say(text)

    def hear(self) -> Heard | None:
        """Wait for the next thing the user says (or types)."""
        return self.ears.listen()

    def _unclear(self, heard: Heard) -> bool:
        """True if speech recognition was not confident enough to act on."""
        return heard.source == "voice" and heard.confidence < self.min_confidence

    # --------------------------------------------------------------- loop --

    def run(self) -> int:
        """The main loop: greet the user, then listen and handle each request until they say
        goodbye or input ends.
        """
        self.speak(WAKE_GREETING.format(wake=self.wake_word.capitalize())
                   if self.wake_word else GREETING)
        try:
            while True:
                heard = self.hear()
                if heard is None:            # input closed
                    break
                if not self.on_utterance(heard):
                    break
        finally:
            self.pipe.close()
        return 0

    def on_utterance(self, heard: Heard) -> bool:
        """Handle one utterance. Returns False when the user ended the session."""
        if heard.empty:
            return True
        text = heard.text.strip()

        if self.wake_word:
            command = strip_wake(text, self.wake_word)
            if command is None:
                # Not addressed to us. Say so on screen (never aloud, or the
                # agent would talk over every conversation in the room).
                self.show(f"(heard \"{text}\" - ignored: start with "
                          f"'{self.wake_word.capitalize()}')")
                return True
            if not command:
                self.speak("Yes?")
                nxt = self.hear()
                if nxt is None:
                    return False
                command = nxt.text.strip()
                heard = nxt
            text = command

        if is_exit(text):
            self.speak("Goodbye.")
            return False

        self.show(f"you: {text}" + (f"   (heard with {heard.confidence:.0%} confidence)"
                                    if heard.source == "voice" else ""))
        if self._unclear(heard):
            self.speak("Sorry, I did not catch that clearly. Please say it again.")
            return True

        self.act(spoken_to_text(text))
        return True

    def act(self, text: str) -> Turn:
        """Run one request through MAESTRO, asking any clarifying questions aloud. Gives up
        (cancels) after too many unclear answers.
        """
        turn = self.pipe.handle(text)
        repeats = 0
        while turn.status == "clarified":
            question = turn.message
            opts = turn.clarification.options if turn.clarification else []
            if opts:
                question += " " + " ".join(f"Option {i}: {o}." for i, o in enumerate(opts, 1))
            self.speak(question)
            answer = self.hear()
            if answer is None:
                break
            if self._unclear(answer) or answer.empty:
                repeats += 1
                if repeats > self.MAX_REPEATS:
                    turn = self.pipe.resume(turn, "cancel")
                    break
                self.speak("Sorry, I did not catch that.")
                continue
            self.show(f"you: {answer.text}")
            turn = self.pipe.resume(turn, answer.text)

        self.turns.append(turn)
        shown = present(turn)
        for line in shown.lines:
            self.show(line)
        spoken = turn.message or turn.status
        if shown.spoken:
            spoken = f"{spoken} {shown.spoken}"
        self.speak(spoken)
        return turn

    # ------------------------------------------------------------ consent --

    def _consent(self, req: ConsentRequest) -> Approval:
        """Ask for approval: show the full preview on screen. A high-risk plan needs the
        confirmation word typed (a spoken yes is not accepted); a lower-risk plan accepts a
        spoken or typed yes.
        """
        self.show("")
        self.show(render_preview(req.plan, req.verdict, req.manifests))
        self.show("")

        if req.gate == "typed_confirm":
            token = req.token or "CONFIRM"
            self.speak("This is a high risk action, so I cannot accept a spoken yes. "
                       "Please type the confirmation shown on screen, or press enter to cancel.")
            self.show(f"  Type exactly:  {token}")
            try:
                typed = self.typed_input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                typed = ""
            if typed and token_matches(typed, token):
                return Approval(True, "typed")
            self.show("  not confirmed")
            return Approval(False, "denied", note="confirmation token did not match")

        self.speak(self._spoken_preview(req) + " Say yes to go ahead, or no to cancel.")
        for _ in range(self.MAX_CONSENT_TRIES):
            heard = self.hear()
            if heard is None:
                break
            self.show(f"you: {heard.text}")
            decision = None if self._unclear(heard) else parse_yes_no(heard.text)
            if decision is True:
                return Approval(True, "voice" if heard.source == "voice" else "click")
            if decision is False:
                return Approval(False, "denied", note="declined")
            self.speak("Please answer yes or no.")
        # Anything short of a clear yes is a no.
        return Approval(False, "denied", note="no clear spoken approval")

    @staticmethod
    def _spoken_preview(req: ConsentRequest) -> str:
        """A short spoken summary of a plan for the approval question: its risk, step count and
        first two steps.
        """
        n = len(req.plan.actions)
        steps = [m.summary for m in req.manifests if m.summary][:2]
        more = f" and {n - len(steps)} more step" + ("s" if n - len(steps) > 1 else "") \
            if n > len(steps) else ""
        return (f"I need your approval. This plan is {str(req.verdict.risk)} risk with "
                f"{n} step{'s' if n != 1 else ''}: " + "; ".join(steps) + more + ".")
