"""The specialist agents (docs/02-ARCHITECTURE.md §5).

    Summarizer  the ONLY component that reads untrusted content — and it has
                no tools and cannot emit Action IR
    Verifier    postcondition checking — plain code, no LLM

The Planner and the Critic live in `maestro/planner/`; the Executors live in
`maestro/executor/`. "Multi-agent" here means specialised components with
distinct responsibilities, context windows and trust levels — not role-played
personas in a chat room.
"""

from maestro.agents.summarizer import (
    INJECTION_PATTERNS,
    InjectionScan,
    Summarizer,
    Summary,
    scan_for_injection,
)
from maestro.agents.verifier import CHECKS, CheckResult, Verifier

__all__ = [
    "INJECTION_PATTERNS",
    "InjectionScan",
    "Summarizer",
    "Summary",
    "scan_for_injection",
    "CHECKS",
    "CheckResult",
    "Verifier",
]
