"""Voice activity detection: turn a stream of audio frames into utterances.

This is the Python port of what the Web-Call-Agent page did in the browser with
`@ricky0123/vad-web`: `onSpeechStart` begins a recording, `onSpeechEnd` stops it
and ships the clip. Here the same decision is a small, pure state machine over
16 kHz mono frames, so it can be tested with synthetic audio and runs with
nothing but numpy.

Two thresholds, as in the original (`positiveSpeechThreshold` /
`negativeSpeechThreshold`): speech must rise clearly above the noise floor to
*start* an utterance, but only has to fall below a lower level to count as
silence. The gap between them (hysteresis) is what stops a single breath or a
fan from chopping one sentence into three.

The noise floor is measured, not assumed: the first frames the microphone
delivers are treated as room tone, and the floor keeps adapting slowly while
nobody is speaking.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of one frame, with its DC offset removed.

    Some USB microphones deliver a constant offset on top of the signal. Left
    in, it reads as loud "noise" in a silent room (a real run measured 0.43
    with nobody speaking), the thresholds scale up with it, and no voice can
    ever cross them. Only the varying part of the signal is sound.
    """
    if frame.size == 0:
        return 0.0
    x = frame.astype(np.float64)
    x = x - x.mean()
    return float(np.sqrt(np.mean(np.square(x))))


@dataclass
class Endpointer:
    """Feed frames in; get a complete utterance out when the speaker stops.

    `feed()` returns None while listening and the utterance (float32 samples)
    once trailing silence has lasted `silence_ms`. Leading audio from just
    before speech began is kept (`preroll_ms`), because the first consonant is
    usually quieter than the trigger level and Whisper needs it.
    """

    start_ratio: float = 3.0      # speech starts at floor x this
    stop_ratio: float = 1.8       # ...and counts as silence below floor x this
    min_level: float = 0.006      # absolute floor, so a dead-silent room still works
    start_ms: int = 90            # loud frames in a row needed to start
    silence_ms: int = 800         # quiet time that ends an utterance
    preroll_ms: int = 300
    min_speech_ms: int = 250      # shorter bursts (a click, a cough) are dropped
    max_utterance_ms: int = 15_000
    calibration_ms: int = 450

    noise_floor: float = 0.0
    _frames_seen: int = 0
    _calib: list[float] = field(default_factory=list)
    _preroll: deque = field(default_factory=deque)
    _speech: list[np.ndarray] = field(default_factory=list)
    _loud_run: int = 0
    _quiet_run: int = 0
    _head: int = 0            # frames in the utterance that came from pre-roll
    _last_level: float = 0.0
    _in_speech: bool = False

    def __post_init__(self) -> None:
        self._preroll = deque(maxlen=max(1, self.preroll_ms // FRAME_MS))

    # -- thresholds --------------------------------------------------------

    @property
    def calibrated(self) -> bool:
        return self._frames_seen * FRAME_MS >= self.calibration_ms

    @property
    def start_level(self) -> float:
        # Capped below full scale: in a loud room, floor x start_ratio can
        # exceed 1.0, a level no voice can reach, and the agent would never
        # hear anything. The cap keeps the threshold reachable.
        ratio = self.noise_floor * self.start_ratio
        reachable = self.noise_floor + 0.3 * (1.0 - self.noise_floor)
        return max(min(ratio, reachable), self.min_level)

    @property
    def stop_level(self) -> float:
        ratio = self.noise_floor * self.stop_ratio
        halfway = self.noise_floor + 0.5 * (self.start_level - self.noise_floor)
        return max(min(ratio, halfway), self.min_level * 0.6)

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    # -- the state machine -------------------------------------------------

    @property
    def level(self) -> float:
        """Level of the most recent frame, for a live meter."""
        return self._last_level

    def current(self) -> np.ndarray | None:
        """The utterance so far, while speech is in progress (for live captions)."""
        if not self._in_speech or not self._speech:
            return None
        return np.concatenate(self._speech).astype(np.float32)

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        level = rms(frame)
        self._last_level = level
        self._frames_seen += 1

        if not self.calibrated:
            self._calib.append(level)
            self.noise_floor = float(np.median(self._calib))
            self._preroll.append(frame)
            return None

        if not self._in_speech:
            self._preroll.append(frame)
            if level >= self.start_level:
                self._loud_run += 1
            else:
                self._loud_run = 0
                # Track the room slowly while nobody is talking.
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * level
            if self._loud_run * FRAME_MS >= self.start_ms:
                self._in_speech = True
                self._speech = list(self._preroll)
                # The trigger frames are genuine speech; the rest of the
                # pre-roll is context and must not count towards min_speech_ms.
                self._head = len(self._speech) - self._loud_run
                self._quiet_run = 0
            return None

        self._speech.append(frame)
        self._quiet_run = self._quiet_run + 1 if level < self.stop_level else 0
        too_long = len(self._speech) * FRAME_MS >= self.max_utterance_ms
        if self._quiet_run * FRAME_MS >= self.silence_ms or too_long:
            return self._finish()
        return None

    def _finish(self) -> np.ndarray | None:
        # Trailing silence is not speech; keep a little of it so the last word
        # is not clipped, drop the rest.
        keep_tail = max(1, 200 // FRAME_MS)
        frames = self._speech[: max(1, len(self._speech) - self._quiet_run + keep_tail)]
        voiced_ms = (len(self._speech) - self._head - self._quiet_run) * FRAME_MS
        self.reset()
        if voiced_ms < self.min_speech_ms:
            return None
        return np.concatenate(frames).astype(np.float32)

    def reset(self) -> None:
        """Forget the current utterance but keep the learned noise floor."""
        self._speech = []
        self._preroll.clear()
        self._loud_run = 0
        self._quiet_run = 0
        self._head = 0
        self._in_speech = False
