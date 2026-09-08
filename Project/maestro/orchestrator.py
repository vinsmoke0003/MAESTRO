"""L2 — the orchestrator: dry-run -> gate -> execute -> verify, in topological order.

The consent flow is structural, not advisory:

* the FULL plan is dry-run before anything executes (docs/06 §3), so the number
  in "move 47 files" is a measured number and the consent means something;
* the plan DAG is **frozen at consent time**. Nothing can be added mid-run
  (docs/02 §6 rule 4). This is the rule teams get wrong because dynamic
  re-planning feels more capable, and it is exactly the hole through which a
  malicious document escalates "summarize" into "exfiltrate";
* a failed step or a failed *postcondition* halts the plan and rolls back the
  completed steps. `ok=True` from an executor is not success — the Verifier
  decides (threat T8);
* the budget guard runs per step, and a breach is treated like a failure.

Every transition is written to the hash-chained audit log, so the run can be
reconstructed afterwards from the log alone (NFR-10).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from maestro.agents.verifier import CheckResult, Verifier
from maestro.executor.base import (
    Context,
    EffectManifest,
    Result,
    UnboundVariable,
    get_executor,
)
from maestro.ir import Plan, Risk
from maestro.planner.critic import Critic, CriticReport
from maestro.safety import PathPolicy
from maestro.safety.audit import AuditLog
from maestro.safety.budget import BudgetExceeded, BudgetGuard
from maestro.safety.consent import Approval, ConsentGate, ConsentRequest
from maestro.safety.scorer import PlanVerdict, score_plan

# Statuses a run can end in. Kept as a closed set because the metrics in
# docs/07 §2 are defined against them.
STATUSES = ("completed", "blocked", "denied", "failed", "rolled_back", "budget_exceeded",
            "previewed")


@dataclass
class StepReport:
    action_id: str
    verb: str
    ok: bool
    detail: str = ""
    ms: float = 0.0
    files_touched: int = 0
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def postconditions_ok(self) -> bool:
        return all(c.ok for c in self.checks)


@dataclass
class RunReport:
    plan_id: str
    status: str = "pending"
    verdict: PlanVerdict | None = None
    steps: list[StepReport] = field(default_factory=list)
    manifests: list[EffectManifest] = field(default_factory=list)
    approval: Approval | None = None
    critic: CriticReport | None = None
    variables: dict = field(default_factory=dict)
    plan_ms: float = 0.0
    exec_ms: float = 0.0
    budget: dict = field(default_factory=dict)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    @property
    def steps_ok(self) -> int:
        return sum(1 for s in self.steps if s.ok)

    @property
    def gate(self) -> str:
        return self.verdict.gate if self.verdict else "unknown"

    @property
    def risk(self) -> Risk:
        return self.verdict.risk if self.verdict else Risk.R0

    def as_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "status": self.status,
            "risk": str(self.risk),
            "gate": self.gate,
            "steps_ok": self.steps_ok,
            "steps_total": len(self.steps),
            "consent_method": self.approval.method if self.approval else "auto",
            "exec_ms": round(self.exec_ms, 1),
            "critic_findings": self.critic.as_dicts() if self.critic else [],
            "message": self.message,
        }


class Orchestrator:
    def __init__(
        self,
        policy: PathPolicy | None = None,
        audit: AuditLog | None = None,
        gate: ConsentGate | None = None,
        verifier: Verifier | None = None,
        critic: Critic | None = None,
        *,
        enable_safety: bool = True,
        enable_dry_run: bool = True,
        enable_postconditions: bool = True,
        enable_critic: bool = True,
    ):
        self.policy = policy or PathPolicy()
        self.audit = audit
        self.gate = gate or ConsentGate()
        self.verifier = verifier or Verifier()
        self.critic = critic or Critic()
        # Ablation switches (docs/07 §5). Each one removes exactly one control
        # so the harness can price it: A1 safety, A2 dry-run, A3 postconditions,
        # A5 critic.
        self.enable_safety = enable_safety
        self.enable_dry_run = enable_dry_run
        self.enable_postconditions = enable_postconditions
        self.enable_critic = enable_critic
        # Optional progress hook, called with each StepReport as it lands. The
        # web UI uses it to stream execution progress; nothing else observes it.
        self.on_step: Callable[[StepReport], None] | None = None

    def _step_done(self, step: StepReport) -> None:
        if self.on_step is not None:
            try:
                self.on_step(step)
            except Exception:  # a broken observer must never fail the run
                pass

    # ---------------------------------------------------------------- run --

    def run(self, plan: Plan, *, episode_id: str | None = None,
            intent: str | None = None, slots=None,
            preview_only: bool = False) -> RunReport:
        self._log("PROPOSED", episode_id, plan_id=plan.plan_id, detail=plan.instruction)
        report = RunReport(plan.plan_id)

        # 1. Deterministic safety verdict — before any side effect.
        verdict = score_plan(plan, self.policy) if self.enable_safety else _permissive(plan)
        report.verdict = verdict

        if verdict.blocked:
            for a in (x for x in verdict.actions if x.blocked):
                self._log("BLOCKED", episode_id, plan_id=plan.plan_id,
                          action_id=a.action_id, verb=a.verb, risk=str(a.risk),
                          detail="; ".join(a.reasons))
            report.status = "blocked"
            report.message = "; ".join(verdict.block_reasons)
            return report

        # 2. Critic review (advisory, shown in the preview).
        if self.enable_critic:
            report.critic = self.critic.review(plan, intent, slots, plan.instruction)

        # 3. Dry-run the whole plan.
        manifests = self._dry_run(plan) if self.enable_dry_run else [
            EffectManifest(summary=f"{a.verb} (dry run disabled)") for a in plan.actions
        ]
        report.manifests = manifests

        # Re-score with real file counts so the bulk rule fires on measurements
        # rather than on nothing. Monotonic: this can only raise risk.
        if self.enable_safety and self.enable_dry_run:
            estimates = {a.action_id: m.files_touched
                         for a, m in zip(plan.actions, manifests, strict=True)}
            verdict = score_plan(plan, self.policy, file_estimates=estimates)
            report.verdict = verdict
            if verdict.blocked:
                report.status = "blocked"
                report.message = "; ".join(verdict.block_reasons)
                return report

        # 4. Budget: static check before consent, so the user sees the scale.
        guard = BudgetGuard(plan.budget)
        breach = guard.check_static(len(plan.actions),
                                    sum(m.files_touched for m in manifests))
        if breach:
            self._log("BUDGET_EXCEEDED", episode_id, plan_id=plan.plan_id, detail=breach)
            report.status = "budget_exceeded"
            report.message = breach
            return report

        # 4b. Preview-only: stop here, before the gate, regardless of tier.
        #
        # `maestro plan` used to reach this point with a consent callback that
        # always said no — which stopped R2/R3 plans and did nothing at all for
        # R0/R1, because those never consult the callback. An R1 "back up the
        # pdfs" plan therefore *executed* under a command called `plan`. Preview
        # has to be a mode of the orchestrator, not a property of the gate.
        if preview_only:
            self._log("PREVIEWED", episode_id, plan_id=plan.plan_id,
                      risk=str(verdict.risk), detail=f"gate={verdict.gate}")
            report.status = "previewed"
            report.message = (f"Preview only — nothing was executed. This plan would "
                              f"need gate '{verdict.gate}'.")
            return report

        # 5. The gate.
        req = ConsentRequest(plan, verdict, manifests)
        if verdict.gate != "auto":
            self._log("GATED", episode_id, plan_id=plan.plan_id, risk=str(verdict.risk),
                      detail=f"gate={verdict.gate}")
        approval = self.gate.decide(req)
        report.approval = approval
        if not approval.approved:
            self._log("DENIED", episode_id, plan_id=plan.plan_id, detail=approval.note)
            report.status = "denied"
            report.message = approval.note or "not approved"
            return report
        if verdict.gate != "auto":
            self._log("APPROVED", episode_id, plan_id=plan.plan_id,
                      detail=f"method={approval.method}")

        # 6. Execute. The DAG is frozen from here; nothing may be added.
        return self._execute(plan, report, guard, episode_id)

    # ------------------------------------------------------------ dry run --

    def _dry_run(self, plan: Plan) -> list[EffectManifest]:
        """Zero side effects, with one deliberate exception: R0 verbs are pure
        reads, so we execute them for real to make the preview concrete ("move
        47 files, 312 MB" instead of "move ? files"). Everything else is
        simulated, and anything unpredictable is reported as unknown rather
        than omitted (docs/06 §3)."""
        # The dry run REALLY executes R0 verbs, and `browser.extract` is R0 — so
        # a plan with browser steps opens a browser session here. That session
        # must be released before real execution starts: left open, its
        # Playwright loop stays alive in this thread and the execution's own
        # `sync_playwright().start()` fails with "Sync API inside the asyncio
        # loop". Every dry-run context is closed, whatever happens inside.
        ctx = Context(dry=True)
        try:
            return self._dry_run_steps(plan, ctx)
        finally:
            ctx.close()

    def _dry_run_steps(self, plan: Plan, ctx: Context) -> list[EffectManifest]:
        from maestro import registry as _registry

        manifests: list[EffectManifest] = []
        for aid in plan.topo_order:
            action = plan.action(aid)
            ex = get_executor(action.verb)
            try:
                manifests.append(ex.dry_run(action.args, ctx))
            except UnboundVariable:
                manifests.append(EffectManifest(
                    summary=f"{action.verb}: effect depends on earlier results",
                    unknowns=["cannot simulate without executing the earlier steps"],
                ))
            except Exception as e:
                manifests.append(EffectManifest(
                    summary=f"{action.verb}: could not simulate",
                    unknowns=[f"{type(e).__name__}: {e}"],
                ))
            if action.produces:
                try:
                    is_read = _registry.get(action.verb).base_risk == Risk.R0
                except _registry.RegistryError:
                    is_read = False
                if is_read:
                    try:
                        r = ex.execute(action.args, ctx)
                        ctx.bind(action.produces, r.output if r.ok else [])
                    except Exception:
                        ctx.bind(action.produces, [])
                else:
                    # Side-effecting producer: a placeholder keeps the shape of
                    # later args resolvable without performing the effect.
                    ctx.bind(action.produces, [])
        # Manifests are returned in plan order, not topological order, so the
        # preview lines up with the action list the user reads.
        order = {aid: i for i, aid in enumerate(plan.topo_order)}
        return [manifests[order[a.action_id]] for a in plan.actions]

    # ------------------------------------------------------------ execute --

    def _execute(self, plan: Plan, report: RunReport, guard: BudgetGuard,
                 episode_id: str | None) -> RunReport:
        # One Context per plan. It owns plan-scoped resources — the browser
        # session above all — and `close()` runs on EVERY exit: normal return,
        # a halted step, a budget breach, or an exception nobody anticipated.
        # This is the lifecycle boundary the browser verbs rely on: they never
        # launch or close a browser themselves.
        ctx = Context()
        try:
            return self._execute_steps(plan, report, guard, episode_id, ctx)
        finally:
            ctx.close()

    def _execute_steps(self, plan: Plan, report: RunReport, guard: BudgetGuard,
                       episode_id: str | None, ctx: Context) -> RunReport:
        undo_stack: list[tuple[str, Result]] = []
        t0 = time.perf_counter()
        guard.reset()

        for aid in plan.topo_order:
            action = plan.action(aid)
            ex = get_executor(action.verb)
            step_t0 = time.perf_counter()

            # Preconditions gate the step.
            pre = self.verifier.run(action.preconditions, ctx) if action.preconditions \
                else []
            if pre and not Verifier.all_ok(pre):
                detail = "precondition failed: " + Verifier.summarize(pre)
                report.steps.append(StepReport(aid, action.verb, False, detail,
                                               (time.perf_counter() - step_t0) * 1000,
                                               checks=pre))
                self._step_done(report.steps[-1])
                self._log("FAILED", episode_id, plan_id=plan.plan_id, action_id=aid,
                          verb=action.verb, detail=detail)
                return self._halt(plan, report, undo_stack, "rolled_back", detail,
                                  episode_id, t0, guard)

            try:
                result = ex.execute(action.args, ctx)
            except UnboundVariable as e:
                result = Result(ok=False, detail=f"unresolvable dataflow: {e}")
            except NotImplementedError as e:
                result = Result(ok=False, detail=f"not implemented: {e}")
            except Exception as e:  # an executor bug fails the step, not the process
                result = Result(ok=False, detail=f"{type(e).__name__}: {e}")

            if action.produces and result.ok:
                ctx.bind(action.produces, result.output)

            # Postconditions decide success (threat T8).
            checks: list[CheckResult] = []
            if result.ok and self.enable_postconditions and action.postconditions:
                checks = self.verifier.run(action.postconditions, ctx)
                if not Verifier.all_ok(checks):
                    result = Result(ok=False, output=result.output,
                                    detail="postcondition failed: "
                                           + Verifier.summarize(checks),
                                    undo_data=result.undo_data,
                                    files_touched=result.files_touched)

            ms = (time.perf_counter() - step_t0) * 1000
            report.steps.append(StepReport(aid, action.verb, result.ok, result.detail,
                                           ms, result.files_touched, checks))
            self._step_done(report.steps[-1])
            self._log("EXECUTED" if result.ok else "FAILED", episode_id,
                      plan_id=plan.plan_id, action_id=aid, verb=action.verb,
                      detail=result.detail)

            if result.undo_data is not None:
                undo_stack.append((aid, result))

            if not result.ok:
                return self._halt(plan, report, undo_stack, "rolled_back", result.detail,
                                  episode_id, t0, guard)

            try:
                guard.tick(result.files_touched)
            except BudgetExceeded as e:
                self._log("BUDGET_EXCEEDED", episode_id, plan_id=plan.plan_id,
                          detail=str(e))
                return self._halt(plan, report, undo_stack, "budget_exceeded", str(e),
                                  episode_id, t0, guard)

        report.status = "completed"
        report.exec_ms = (time.perf_counter() - t0) * 1000
        report.variables = dict(ctx.variables)
        report.budget = guard.summary()
        return report

    def _halt(self, plan: Plan, report: RunReport, undo_stack, status: str,
              message: str, episode_id: str | None, t0: float,
              guard: BudgetGuard) -> RunReport:
        self._rollback(plan, undo_stack, episode_id)
        report.status = status
        report.message = message
        report.exec_ms = (time.perf_counter() - t0) * 1000
        report.budget = guard.summary()
        return report

    def _rollback(self, plan: Plan, undo_stack, episode_id: str | None) -> None:
        for aid, result in reversed(undo_stack):
            action = plan.action(aid)
            try:
                get_executor(action.verb).undo(result, Context())
                self._log("UNDONE", episode_id, plan_id=plan.plan_id, action_id=aid,
                          verb=action.verb)
            except NotImplementedError as e:
                self._log("FAILED", episode_id, plan_id=plan.plan_id, action_id=aid,
                          verb=action.verb,
                          detail=f"undo not available ({e}); manual recovery needed")
            except Exception as e:
                self._log("FAILED", episode_id, plan_id=plan.plan_id, action_id=aid,
                          verb=action.verb, detail=f"undo failed: {e}")

    # -- session-level undo (FR-29) ---------------------------------------

    def undo_run(self, plan: Plan, report: RunReport | None = None, *,
                 variables: dict | None = None,
                 executed: list[str] | None = None) -> UndoReport:
        """Reverse a completed run, and say exactly what happened to each step.

        Works from a live `RunReport` (same process) OR from persisted state —
        `variables` and the list of executed action ids — so `maestro undo` can
        reverse a plan after the terminal that ran it has been closed. Steps
        are reversed in the opposite order to execution.

        Every action gets one of four outcomes, because "undo did something"
        is not an answer a user can act on:
            reversed     the declared inverse ran and reported success
            skipped      the action declares no undo (read-only, or genuinely
                         irreversible — `fs.trash` says so explicitly)
            failed       the inverse ran and reported a problem (e.g. a moved
                         file is no longer where we put it)
            verified     for moves: contents re-hashed and matched move time
        """
        vars_ = dict(variables if variables is not None
                     else (report.variables if report else {}))
        if executed is None:
            executed = [s.action_id for s in (report.steps if report else []) if s.ok]

        out = UndoReport(plan_id=plan.plan_id)
        for aid in reversed(executed):
            action = plan.action(aid)
            if action.undo is None:
                spec = _spec_or_none(action.verb)
                if spec and spec.base_risk == Risk.R0:
                    why = "read-only; nothing to reverse"
                elif action.verb == "fs.mkdir":
                    why = "created directory left in place (empty; harmless to keep)"
                elif spec and not spec.reversible:
                    why = "irreversible by nature — cannot be undone"
                else:
                    why = "declares no inverse in this plan; left as executed"
                out.skipped.append((aid, action.verb, why))
                continue
            try:
                ex = get_executor(action.undo.verb)
            except KeyError:
                out.failed.append((aid, action.verb, f"no executor for {action.undo.verb}"))
                continue
            try:
                res = ex.execute(action.undo.args, Context(variables=vars_))
            except Exception as e:  # an inverse must never crash the undo
                res = Result(ok=False, detail=f"{type(e).__name__}: {e}")
            if res.ok:
                out.reversed += 1
                out.details.append((aid, action.undo.verb, res.detail))
                if isinstance(res.output, dict) and "mismatched" in res.output:
                    out.verified_files += len(res.output.get("restored", []))
                    out.mismatched_files += len(res.output.get("mismatched", []))
                    out.collisions += len(res.output.get("collided", []))
                self._log("UNDONE", None, plan_id=plan.plan_id, action_id=aid,
                          verb=action.undo.verb, detail=res.detail)
            else:
                out.failed.append((aid, action.undo.verb, res.detail))
                self._log("FAILED", None, plan_id=plan.plan_id, action_id=aid,
                          verb=action.undo.verb, detail=f"undo failed: {res.detail}")
        return out

    # -- audit -------------------------------------------------------------

    def _log(self, event: str, episode_id: str | None = None, **kw) -> None:
        if self.audit:
            self.audit.append(event, episode_id=episode_id, **kw)


def _permissive(plan: Plan) -> PlanVerdict:
    """Ablation A1: the safety layer removed. Everything scores R0 / auto.

    This is what B1 "single LLM, direct execution, no safety layer" looks like
    inside our own harness, so the comparison in docs/07 §4 measures the same
    executors and the same tasks — only the safety layer differs.
    """
    from maestro.safety.scorer import ActionVerdict

    return PlanVerdict(plan.plan_id, [
        ActionVerdict(a.action_id, a.verb, Risk.R0, ["safety layer disabled (ablation A1)"])
        for a in plan.actions
    ])


@dataclass
class UndoReport:
    """What `undo_run` did, step by step, in language the CLI can show."""

    plan_id: str
    reversed: int = 0
    verified_files: int = 0      # files whose restored bytes matched the move-time hash
    mismatched_files: int = 0    # restored, but the bytes differ — reported, never hidden
    collisions: int = 0          # origins that were occupied; restored beside them
    skipped: list[tuple[str, str, str]] = field(default_factory=list)   # (aid, verb, why)
    failed: list[tuple[str, str, str]] = field(default_factory=list)    # (aid, verb, why)
    details: list[tuple[str, str, str]] = field(default_factory=list)   # (aid, inverse, detail)

    @property
    def ok(self) -> bool:
        return not self.failed and self.mismatched_files == 0

    @property
    def fully_reversible(self) -> bool:
        """True when nothing had to be skipped as irreversible."""
        return not any("irreversible" in why for _, _, why in self.skipped)

    def render(self) -> str:
        lines = [f"Undo of plan {self.plan_id}: {self.reversed} step(s) reversed"]
        for aid, inverse, detail in self.details:
            lines.append(f"  v {aid} via {inverse}: {detail}")
        if self.verified_files:
            lines.append(f"  {self.verified_files} restored file(s) re-hashed and "
                         f"verified against their move-time contents")
        if self.collisions:
            lines.append(f"  {self.collisions} origin(s) were occupied by newer files; "
                         f"restored beside them as 'name (1).ext' — nothing overwritten")
        for aid, verb, why in self.skipped:
            lines.append(f"  - {aid} {verb}: not reversed — {why}")
        for aid, verb, why in self.failed:
            lines.append(f"  ! {aid} {verb}: FAILED — {why}")
        if self.mismatched_files:
            lines.append(f"  ! {self.mismatched_files} file(s) differ from their move-time "
                         f"hash — inspect before trusting them")
        return "\n".join(lines)


def _spec_or_none(verb: str):
    from maestro import registry as _registry

    try:
        return _registry.get(verb)
    except _registry.RegistryError:
        return None


# --------------------------------------------------------------------------- #
# the consent preview
# --------------------------------------------------------------------------- #

_TAG = {Risk.R0: "safe", Risk.R1: "low", Risk.R2: "MEDIUM", Risk.R3: "HIGH",
        Risk.BLOCKED: "BLOCKED"}


def render_preview(plan: Plan, verdict: PlanVerdict, manifests: list[EffectManifest],
                   critic: CriticReport | None = None) -> str:
    """Human-readable preview — what the user actually consents to (docs/06 §3)."""
    lines = [f'MAESTRO will perform {len(plan.actions)} action(s) for:',
             f'  "{plan.instruction}"', ""]
    total_files = total_bytes = 0
    for av, m in zip(verdict.actions, manifests, strict=True):
        lines.append(f"  {av.action_id}. {m.summary}   [{av.risk} {_TAG[av.risk]}]")
        if m.files_touched:
            lines.append(f"      -> {m.files_touched} file(s), {m.bytes_affected:,} bytes")
            total_files += m.files_touched
            total_bytes += m.bytes_affected
        for c in m.collisions:
            lines.append(f"      ! collision: {c}")
        for x in m.external:
            lines.append(f"      > network: {x}")
        for u in m.unknowns:
            lines.append(f"      ? {u}")
        if av.risk >= Risk.R2 and av.reasons:
            lines.append(f"      why: {av.reasons[-1]}")

    if critic and not critic.clean:
        lines.append("")
        lines.append(critic.render())

    lines.append("")
    if total_files:
        lines.append(f"  Total: {total_files} file(s), {total_bytes:,} bytes")
    lines.append(f"  Plan risk: {verdict.risk} -> gate: {verdict.gate}")
    undoable = sum(1 for a in plan.actions if a.undo is not None)
    if undoable:
        lines.append(f"  {undoable} of {len(plan.actions)} action(s) declare an undo")
    return "\n".join(lines)
