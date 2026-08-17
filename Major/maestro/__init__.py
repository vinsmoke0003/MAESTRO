"""MAESTRO — Safe Multi-Agent System for Natural Language-Driven Desktop Task Automation.

Package layout mirrors docs/02-ARCHITECTURE.md:

    ir/        Action IR — the typed contract between planner and executors
    registry   Closed verb registry (unknown verb == rejected plan)
    safety/    Deterministic risk scoring, path policy, audit log  <- research contribution
    executor/  Per-verb executors with mandatory dry_run()
    orchestrator  Topological DAG execution with undo stack
"""

__version__ = "0.1.0"
