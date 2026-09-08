"""MAESTRO — Safe Multi-Agent System for Natural Language-Driven Desktop Task Automation.

Major project (8th semester) implementation. Package layout mirrors the layered
view in docs/02-ARCHITECTURE.md §2:

    ir/            L4/L3 contract — the typed Action IR
    registry       Closed verb registry (unknown verb == rejected plan)
    nlp/           L5 — intent classification, entity extraction, clarification
    planner/       L4 — LLM planner, rule-based baseline, critic
    safety/        L3 — path policy, deterministic risk scorer, taint, audit  ★ novelty
    orchestrator   L2 — DAG scheduling, postconditions, budget, undo stack
    executor/      L1 — per-verb executors (portable + win32/darwin backends)
    memory/        L0 — episodes, preferences, semantic exemplar store
    agents/        Summarizer (tool-less, reads untrusted content) + Verifier
    pipeline       The whole request lifecycle wired together (docs/02 §4)

Nothing above `executor/` may branch on the platform. `scripts/check_platform_boundary.py`
enforces that in CI.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
