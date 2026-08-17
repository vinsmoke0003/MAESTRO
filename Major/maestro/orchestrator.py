"""Orchestrator: dry-run -> gate -> execute, in topological order.

The consent flow is structural, not advisory:
  - the FULL plan is dry-run before anything executes (docs/06 §3)
  - the plan DAG is frozen at consent time; nothing can be added mid-run
  - a failed step halts the plan and offers rollback of completed steps
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from maestro.executor.base import Context, EffectManifest, Result, get_executor
from maestro.ir import Plan, Risk
from maestro.safety import PathPolicy, PlanVerdict, score_plan
from maestro.safety.audit import AuditLog

# Callback the UI supplies: shown the preview, returns True to approve.
# Automated tests / R0-R1 plans never invoke it.
ConsentFn = Callable[[Plan, PlanVerdict, list[EffectManifest]], bool]


@dataclass
class StepReport:
    action_id: str
    verb: str
    ok: bool
    detail: str = ""


@dataclass
class RunReport:
    plan_id: str
    status: str  # completed | blocked | denied | failed | rolled_back
    verdict: PlanVerdict | None = None
    steps: list[StepReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class Orchestrator:
    def __init__(
        self,
        policy: PathPolicy | None = None,
        audit: AuditLog | None = None,
        consent: ConsentFn | None = None,
    ):
        self.policy = policy or PathPolicy()
        self.audit = audit
        self.consent = consent

    def run(self, plan: Plan) -> RunReport:
        self._log("PROPOSED", plan_id=plan.plan_id, detail=plan.instruction)

        # 1. Deterministic safety verdict — before any side effect.
        verdict = score_plan(plan, self.policy)
        if verdict.blocked:
            blocked = [a for a in verdict.actions if a.blocked]
            for a in blocked:
                self._log("BLOCKED", plan_id=plan.plan_id, action_id=a.action_id,
                          verb=a.verb, detail="; ".join(a.reasons))
            return RunReport(plan.plan_id, "blocked", verdict)

        # 2. Dry-run the whole plan. Zero side effects: R0 verbs are pure
        #    reads, so we execute them for real to make the preview concrete
        #    ("move 47 files, 312 MB" instead of "move ? files"); everything
        #    else is simulated only.
        from maestro import registry as _registry

        ctx_dry = Context()
        manifests: list[EffectManifest] = []
        for aid in plan.topo_order:
            action = plan.action(aid)
            ex = get_executor(action.verb)
            try:
                manifests.append(ex.dry_run(action.args, ctx_dry))
            except KeyError:
                # A $var whose producer only yields a value at real execution:
                # report honestly instead of guessing (docs/06 §3).
                manifests.append(EffectManifest(
                    summary=f"{action.verb}: effect depends on earlier results",
                    unknowns=["cannot simulate without executing prior steps"],
                ))
            if action.produces:
                if _registry.get(action.verb).base_risk == Risk.R0:
                    r = ex.execute(action.args, ctx_dry)  # read-only by tier
                    ctx_dry.bind(action.produces, r.output if r.ok else [])
                else:
                    # Side-effecting producer: placeholder keeps shape resolvable.
                    ctx_dry.bind(action.produces, [])

        # 3. Gate.
        if verdict.gate in ("confirm", "typed_confirm"):
            self._log("GATED", plan_id=plan.plan_id, detail=f"risk={verdict.risk}")
            approved = self.consent(plan, verdict, manifests) if self.consent else False
            if not approved:
                self._log("DENIED", plan_id=plan.plan_id)
                return RunReport(plan.plan_id, "denied", verdict)
            self._log("APPROVED", plan_id=plan.plan_id)

        # 4. Execute in topological order with postcondition-by-construction
        #    (executors return ok=False rather than raising on expected misses).
        ctx = Context()
        undo_stack: list[tuple[str, Result]] = []
        steps: list[StepReport] = []
        for aid in plan.topo_order:
            action = plan.action(aid)
            ex = get_executor(action.verb)
            try:
                result = ex.execute(action.args, ctx)
            except Exception as e:  # executor bug: fail the step, keep state sane
                result = Result(ok=False, detail=f"{type(e).__name__}: {e}")
            steps.append(StepReport(aid, action.verb, result.ok, result.detail))
            self._log("EXECUTED" if result.ok else "FAILED", plan_id=plan.plan_id,
                      action_id=aid, verb=action.verb, detail=result.detail)
            if not result.ok:
                self._rollback(undo_stack, plan)
                return RunReport(plan.plan_id, "rolled_back", verdict, steps)
            if action.produces:
                ctx.bind(action.produces, result.output)
            if result.undo_data is not None:
                undo_stack.append((aid, result))

        return RunReport(plan.plan_id, "completed", verdict, steps)

    def _rollback(self, undo_stack: list[tuple[str, Result]], plan: Plan) -> None:
        for aid, result in reversed(undo_stack):
            action = plan.action(aid)
            try:
                get_executor(action.verb).undo(result, Context())
                self._log("UNDONE", plan_id=plan.plan_id, action_id=aid, verb=action.verb)
            except NotImplementedError:
                self._log("FAILED", plan_id=plan.plan_id, action_id=aid,
                          verb=action.verb, detail="undo not available; manual recovery")

    def _log(self, event: str, **kw) -> None:
        if self.audit:
            self.audit.append(event, **kw)


def render_preview(plan: Plan, verdict: PlanVerdict, manifests: list[EffectManifest]) -> str:
    """Human-readable preview — what the user actually consents to."""
    lines = [f"MAESTRO will perform {len(plan.actions)} action(s) "
             f"for: \"{plan.instruction}\"", ""]
    for av, m in zip(verdict.actions, manifests):
        tag = {Risk.R0: "safe", Risk.R1: "low", Risk.R2: "MEDIUM", Risk.R3: "HIGH"}[av.risk]
        lines.append(f"  {av.action_id}. {m.summary}   [{av.risk} {tag}]")
        if m.files_touched:
            lines.append(f"      -> {m.files_touched} file(s), {m.bytes_affected:,} bytes")
        for c in m.collisions:
            lines.append(f"      ⚠ collision: {c}")
        for u in m.unknowns:
            lines.append(f"      ? {u}")
    lines.append("")
    lines.append(f"  Plan risk: {verdict.risk} -> gate: {verdict.gate}")
    return "\n".join(lines)
