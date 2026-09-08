"""Budget guard — control for threat T7, resource exhaustion (docs/06 §1).

A plan declares a budget at plan time; the guard enforces it at two moments:

  * statically, before consent — "this plan would touch 12,000 files" is
    something the user must see *before* approving, not discover afterwards;
  * dynamically, during execution — wall-clock and files-touched are counted
    as the DAG runs, and the plan is halted the moment a limit is crossed.

Halting mid-plan is safe because the orchestrator rolls the completed steps
back; a budget breach is treated exactly like a step failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from maestro.ir import Budget


class BudgetExceeded(RuntimeError):
    """Raised by the guard; caught by the orchestrator, which then rolls back."""


@dataclass
class BudgetGuard:
    budget: Budget
    started_at: float = field(default_factory=time.monotonic)
    steps: int = 0
    files: int = 0

    def reset(self) -> BudgetGuard:
        self.started_at = time.monotonic()
        self.steps = 0
        self.files = 0
        return self

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    # -- static check, before consent -------------------------------------

    def check_static(self, n_actions: int, estimated_files: int) -> str | None:
        if n_actions > self.budget.max_steps:
            return f"plan has {n_actions} steps, budget allows {self.budget.max_steps}"
        if estimated_files > self.budget.max_files_touched:
            return (
                f"plan would touch ~{estimated_files} files, budget allows "
                f"{self.budget.max_files_touched}"
            )
        return None

    # -- dynamic check, per step ------------------------------------------

    def tick(self, files_touched: int = 0) -> None:
        self.steps += 1
        self.files += max(0, files_touched)
        if self.steps > self.budget.max_steps:
            raise BudgetExceeded(
                f"step budget exceeded ({self.steps} > {self.budget.max_steps})"
            )
        if self.files > self.budget.max_files_touched:
            raise BudgetExceeded(
                f"file budget exceeded ({self.files} > {self.budget.max_files_touched})"
            )
        if self.elapsed_s > self.budget.max_seconds:
            raise BudgetExceeded(
                f"time budget exceeded ({self.elapsed_s:.1f}s > {self.budget.max_seconds}s)"
            )

    def summary(self) -> dict[str, float]:
        return {
            "steps": self.steps,
            "files": self.files,
            "elapsed_s": round(self.elapsed_s, 3),
            "max_steps": self.budget.max_steps,
            "max_files": self.budget.max_files_touched,
            "max_seconds": self.budget.max_seconds,
        }
