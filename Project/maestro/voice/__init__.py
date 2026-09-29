"""Voice interface: speak to MAESTRO, hear it answer (`maestro voice`).

Local and free by construction: speech recognition runs on this machine
(faster-whisper), speech output uses the operating system's own voice, and the
planner is the same local-first pipeline as every other interface. See
`agent.py` for the controls voice adds against mishearing.
"""

from maestro.voice.agent import VoiceAgent, is_exit, parse_yes_no, strip_wake
from maestro.voice.ears import (
    Heard,
    KeyboardEars,
    MicEars,
    ScriptedEars,
    VoiceUnavailable,
    WhisperTranscriber,
)
from maestro.voice.mouth import RecordingMouth, SilentMouth, SystemMouth, speakable


def __getattr__(name: str):
    # The endpointer needs numpy; keep `--text` mode working on a core install.
    if name == "Endpointer":
        from maestro.voice.vad import Endpointer

        return Endpointer
    raise AttributeError(name)


__all__ = [
    "Endpointer",
    "Heard",
    "KeyboardEars",
    "MicEars",
    "RecordingMouth",
    "ScriptedEars",
    "SilentMouth",
    "SystemMouth",
    "VoiceAgent",
    "VoiceUnavailable",
    "WhisperTranscriber",
    "is_exit",
    "parse_yes_no",
    "speakable",
    "strip_wake",
]
