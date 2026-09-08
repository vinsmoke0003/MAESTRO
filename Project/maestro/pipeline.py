"""The request lifecycle, end to end (docs/02-ARCHITECTURE.md §4).

    text ──▶ L5 NLP ──▶ [clarify?] ──▶ L4 plan ──▶ L3 safety ──▶ preview
         ──▶ consent gate ──▶ L2 execute+verify ──▶ L0 memory ──▶ answer

This module is the only place that knows the order of those steps. The CLI, the
API and the evaluation harness all call `MaestroPipeline.handle()`, which is why
the benchmark measures the same code path a user drives — a harness that
reimplemented the lifecycle would be measuring a system nobody runs.

Everything is injectable (planner, gate, policy, stores, ablation switches)
because docs/07 §5 needs to remove one component at a time and keep the rest
identical.
"""

from __future__ import annotations

import json
import platform as _platform
import time
from dataclasses import dataclass, field
from pathlib import Path

from maestro.agents.summarizer import Summarizer
from maestro.config import Settings, settings
from maestro.ir import Plan
from maestro.llm import router
from maestro.memory import EpisodeStore, PreferenceStore, open_store
from maestro.nlp import (
    ClarificationManager,
    EntityExtractor,
    IntentPrediction,
    Slots,
    load_classifier,
)
from maestro.nlp.clarify import Clarification
from maestro.orchestrator import Orchestrator, RunReport, render_preview
from maestro.planner import Critic, HybridPlanner, Planner, PlannerError
from maestro.safety import AuditLog, ConsentGate, PathPolicy
from maestro.safety.consent import Approval, ConsentRequest

REFUSAL_TEXT = {
    "UNSAFE_REQUEST": (
        "I can't do that. MAESTRO refuses this class of request outright — it has no "
        "override flag, by design. Permanent deletion, sending messages on your behalf, "
        "entering credentials, purchases, privilege escalation and shell execution are "
        "hard-blocked (see the safety spec, §5). Reversible alternatives exist for most "
        "of them: I can move files to the Recycle Bin, or write an email draft for you "
        "to review and send yourself."
    ),
    "OUT_OF_SCOPE": (
        "That is outside what MAESTRO does. I automate desktop tasks: files and folders, "
        "search, the browser, applications, system info, and drafting. I am not a general "
        "chat assistant."
    ),
}


@dataclass
class Turn:
    """Everything one instruction produced. The harness scores this object."""

    instruction: str
    episode_id: str = ""
    intent: str = ""
    intent_confidence: float = 0.0
    slots: Slots | None = None
    clarification: Clarification | None = None
    plan: Plan | None = None
    report: RunReport | None = None
    status: str = ""  # clarified | refused | completed | denied | blocked | failed | ...
    message: str = ""
    preview: str = ""
    plan_ms: float = 0.0
    total_ms: float = 0.0
    strategy: str = ""
    error: str = ""
    # Carried across a clarification so the answer can be applied to the SAME
    # instruction rather than the user retyping it.
    slot_overrides: dict = field(default_factory=dict)
    intent_override: str | None = None
    rounds: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    @property
    def gate(self) -> str:
        return self.report.gate if self.report else "none"

    @property
    def risk(self) -> str:
        return str(self.report.risk) if self.report else "R0"

    def as_dict(self) -> dict:
        return {
            "instruction": self.instruction,
            "episode_id": self.episode_id,
            "intent": self.intent,
            "intent_confidence": round(self.intent_confidence, 4),
            "status": self.status,
            "risk": self.risk,
            "gate": self.gate,
            "strategy": self.strategy,
            "clarified": bool(self.clarification and self.clarification.needed),
            "plan_ms": round(self.plan_ms, 1),
            "total_ms": round(self.total_ms, 1),
            "verbs": self.plan.verb_sequence() if self.plan else [],
            "steps_ok": self.report.steps_ok if self.report else 0,
            "steps_total": len(self.report.steps) if self.report else 0,
            "message": self.message,
            "error": self.error,
        }


@dataclass
class MaestroPipeline:
    cfg: Settings = field(default_factory=settings)
    policy: PathPolicy | None = None
    gate: ConsentGate | None = None
    planner: object | None = None
    audit: AuditLog | None = None
    episodes: EpisodeStore | None = None
    prefs: PreferenceStore | None = None
    vectors: object | None = None
    # ablation switches (docs/07 §5)
    enable_safety: bool = True
    enable_dry_run: bool = True
    enable_postconditions: bool = True
    enable_critic: bool = True
    enable_memory: bool = True
    use_trained_intent: bool = True
    # The deterministic unsafe-intent prefilter (maestro/nlp/classifier.py).
    # It lives in L5, not L3, so switching off the safety layer does NOT switch
    # it off — which is architecturally correct but makes the "no safety layer"
    # baselines look far better than the naive agents they are meant to model.
    # B1/B2/A8 turn it off explicitly so the comparison is honest.
    # NEVER set this False outside the evaluation harness.
    enable_prefilter: bool = True

    def __post_init__(self) -> None:
        self.cfg.ensure_dirs()
        self.policy = self.policy or PathPolicy()
        self.audit = self.audit if self.audit is not None else AuditLog(self.cfg.audit_db)
        self.episodes = (self.episodes if self.episodes is not None
                         else EpisodeStore(self.cfg.episodes_db))
        self.prefs = (self.prefs if self.prefs is not None
                      else PreferenceStore(self.cfg.episodes_db))
        if self.vectors is None and self.enable_memory:
            self.vectors = open_store(self.cfg.vector_dir, prefer_chroma=False)

        self.classifier = (load_classifier() if self.use_trained_intent
                           else load_classifier(Path("/nonexistent")))
        self.extractor = EntityExtractor(known_paths=self._known_paths())
        self.clarifier = ClarificationManager()
        self.critic = Critic()

        if self.planner is None:
            self.planner = self._default_planner()

        self.orchestrator = Orchestrator(
            policy=self.policy,
            audit=self.audit,
            gate=self.gate or ConsentGate(),
            critic=self.critic,
            enable_safety=self.enable_safety,
            enable_dry_run=self.enable_dry_run,
            enable_postconditions=self.enable_postconditions,
            enable_critic=self.enable_critic,
        )
        self.summarizer = Summarizer(self._llm_client())

    # ------------------------------------------------------------- wiring --

    def _default_planner(self):
        backend = router.pick(self.cfg)
        llm = (Planner(backend.client, home=Path.home()) if backend.available else None)
        self.backend_note = backend.note
        self.backend_name = backend.name
        return HybridPlanner(llm=llm)

    def _llm_client(self):
        p = self.planner
        llm = getattr(p, "llm", None)
        return getattr(llm, "client", None)

    def _predict_without_prefilter(self, instruction: str) -> IntentPrediction:
        """Classify with the deterministic refusal filter bypassed.

        Exists only so the "no safety layer" baselines in docs/07 §4 actually
        model a system without one. Monkeypatching the module-level filter would
        leak across configs in the same process, so the bypass is explicit and
        local: run the classifier's own model path and take whatever it says.
        """
        from maestro.nlp import classifier as clf_mod

        real = clf_mod.safety_prefilter
        clf_mod.safety_prefilter = lambda _text: None
        try:
            return self.classifier.predict(instruction)
        finally:
            clf_mod.safety_prefilter = real

    def _known_paths(self) -> dict[str, str]:
        if not self.enable_memory or self.prefs is None:
            return {}
        try:
            return self.prefs.known_paths()
        except Exception:
            return {}

    def describe(self) -> str:
        return (f"MAESTRO {_version()} · {router.describe(self.cfg)} · "
                f"intent={self.classifier.name} · workspace={self.cfg.workspace}")

    # -------------------------------------------------------------- handle --

    def handle(self, instruction: str, *, input_mode: str = "text",
               allow_clarify: bool = True, preview_only: bool = False,
               slot_overrides: dict | None = None,
               intent_override: str | None = None,
               expected_fingerprint: str | None = None) -> Turn:
        """One instruction, start to finish.

        `slot_overrides` and `intent_override` are how a clarifying answer is
        applied: the original instruction is re-run with the missing detail
        filled in, so the user answers a question instead of retyping the
        task. They come only from `resume()`, i.e. from the user's own answer
        — never from file contents or page text (docs/02 §6).

        `expected_fingerprint` binds this run to a plan the user has already
        looked at (the web workspace previews first, then executes). If the
        re-planned plan's canonical fingerprint differs, the turn ends
        "denied" BEFORE the orchestrator runs — for every risk tier. Checking
        only inside the consent callback would miss R0/R1 plans, which never
        consult it.
        """
        t_start = time.perf_counter()
        turn = Turn(instruction=instruction)
        eid = self.episodes.new_id() if self.episodes else ""
        turn.episode_id = eid

        # ---- L5: intent + entities ---------------------------------------
        pred: IntentPrediction = (self.classifier.predict(instruction)
                                  if self.enable_prefilter
                                  else self._predict_without_prefilter(instruction))
        if intent_override and not pred.is_confident_refusal:
            # The user chose an interpretation ("organise by type"), which is
            # better evidence than the classifier's guess. A confident refusal
            # is never overridden this way.
            pred = IntentPrediction(intent_override, 1.0, {intent_override: 1.0},
                                    "user")
        turn.intent = pred.intent
        turn.intent_confidence = pred.confidence
        slots = self.extractor.slots(instruction, pred.intent)
        for key, value in (slot_overrides or {}).items():
            if hasattr(slots, key):
                setattr(slots, key, value)
                if key in slots.unresolved:
                    slots.unresolved.remove(key)
        turn.slots = slots

        # ---- refusal (a decision, not an error) --------------------------
        # `is_confident_refusal`, not `is_refusal`: an unmatched instruction
        # also lands in OUT_OF_SCOPE, and "no rule fired" must produce a
        # question, not a refusal.
        #
        # `enable_prefilter` gates the whole refusal branch, not just the
        # deterministic filter. Bypassing only the filter was not enough for the
        # B1 baseline: the *trained* classifier has learned the UNSAFE_REQUEST
        # class from the dataset and goes on predicting it, so the naive-agent
        # baseline kept refusing and the comparison measured nothing.
        if pred.is_confident_refusal and self.enable_prefilter:
            turn.status = "refused"
            turn.message = REFUSAL_TEXT.get(pred.intent, "I can't do that.")
            self._audit("BLOCKED", eid, detail=f"{pred.intent}: {instruction[:120]}")
            self._record(turn, "blocked", eid, pred, slots)
            turn.total_ms = (time.perf_counter() - t_start) * 1000
            return turn

        # ---- FR-06: ask instead of guessing ------------------------------
        clar = self.clarifier.check(instruction, pred, slots)
        turn.clarification = clar
        turn.slot_overrides = dict(slot_overrides or {})
        turn.intent_override = intent_override
        if clar.needed and allow_clarify:
            turn.status = "clarified"
            turn.message = clar.question
            self._audit("CLARIFY", eid, detail=clar.reason)
            self._record(turn, "clarified", eid, pred, slots)
            turn.total_ms = (time.perf_counter() - t_start) * 1000
            return turn

        # ---- L4: plan -----------------------------------------------------
        t_plan = time.perf_counter()
        try:
            self._attach_exemplars(instruction, pred.intent)
            plan = self.planner.plan(instruction, pred.intent, slots)  # type: ignore[union-attr]
        except PlannerError as e:
            turn.status = "failed"
            turn.error = str(e)
            turn.message = f"I could not build a plan for that. {e}"
            self._record(turn, "failed", eid, pred, slots)
            turn.total_ms = (time.perf_counter() - t_start) * 1000
            return turn
        turn.plan_ms = (time.perf_counter() - t_plan) * 1000
        turn.plan = plan
        turn.strategy = getattr(self.planner, "last_strategy", "") or plan.planner.strategy

        if expected_fingerprint is not None and plan.fingerprint() != expected_fingerprint:
            turn.status = "denied"
            turn.message = ("The plan changed between preview and approval — nothing was "
                            "executed. Preview again and approve what you see.")
            turn.error = f"fingerprint {plan.fingerprint()} != previewed {expected_fingerprint}"
            if self.orchestrator.audit:
                self.orchestrator.audit.append("DENIED", episode_id=eid, plan_id=plan.plan_id,
                                               detail="plan differs from the previewed plan")
            self._record(turn, "denied", eid, pred, slots)
            turn.total_ms = (time.perf_counter() - t_start) * 1000
            return turn

        # ---- L3 + L2: safety, gate, execute, verify -----------------------
        report = self.orchestrator.run(plan, episode_id=eid, intent=pred.intent,
                                       slots=slots, preview_only=preview_only)
        turn.report = report
        turn.status = report.status
        turn.preview = render_preview(plan, report.verdict, report.manifests,
                                      report.critic) if report.verdict else ""
        turn.message = _outcome_message(report)

        # ---- L0: remember --------------------------------------------------
        self._record(turn, report.status, eid, pred, slots)
        if report.ok and self.enable_memory and not preview_only:
            self._learn(instruction, plan, pred.intent)

        turn.total_ms = (time.perf_counter() - t_start) * 1000
        return turn

    # ------------------------------------------------------- conversation --

    MAX_ROUNDS = 4
    CANCEL_WORDS = frozenset({"cancel", "stop", "quit", "q", "never mind", "nevermind",
                              "forget it", "no", "n"})

    def resume(self, pending: Turn, answer: str, *, preview_only: bool = False) -> Turn:
        """Apply the user's answer to a clarified turn and continue the task.

        Returns a new Turn for the SAME instruction. Three outcomes:
          * the task proceeds (planned, gated, executed as usual);
          * another question, if the answer left a slot unresolved — bounded
            by MAX_ROUNDS so a confused exchange cannot loop forever;
          * status "cancelled" if the answer is a cancel word, the round limit
            is hit, or the answer could not be understood and there is nothing
            sensible left to ask.
        An answer that cannot be interpreted is reported as such, with the
        question repeated — never silently guessed (docs/05 §1).
        """
        answer = (answer or "").strip()
        clar = pending.clarification
        if clar is None or not clar.needed:
            return pending
        if answer.lower() in self.CANCEL_WORDS or not answer:
            return self._cancelled(pending, "cancelled — nothing was changed.")
        if pending.rounds + 1 >= self.MAX_ROUNDS:
            return self._cancelled(pending, "cancelled after several unclear answers — "
                                            "nothing was changed. Try one full sentence.")

        overrides = dict(pending.slot_overrides)
        intent_override = pending.intent_override
        problem: str | None = None

        if clar.reason == "low_confidence":
            # The user rephrased. Treat the answer as the instruction itself.
            fresh = self.handle(answer, preview_only=preview_only)
            fresh.rounds = pending.rounds + 1
            return fresh

        if clar.reason == "missing_slot" or (clar.reason == "ambiguous_destructive"
                                             and clar.slot == "file_type"):
            value, problem = self._interpret_answer(clar.slot or "", answer)
            if value is not None:
                if isinstance(value, dict):
                    overrides.update(value)       # e.g. {"days": 365}
                else:
                    overrides[clar.slot or ""] = value

        elif clar.reason == "ambiguous_destructive":
            choice = _match_option(answer, clar.options)
            # Compare against the lower-cased option: "move to Recycle Bin" has a
            # capital R and B, and a case-sensitive `"recycle" in choice` matched
            # nothing — so no override was set and the same question came back.
            chosen = (choice or "").lower()
            if choice is None:
                problem = ("pick one of: " + ", ".join(clar.options)
                           if clar.options else "say what you want done")
            elif "type" in chosen:
                intent_override = "FILE_ORGANIZE"
                overrides["group_by"] = "type"
            elif "archive" in chosen:
                intent_override = "FILE_ORGANIZE"
                overrides["destination"] = "~/maestro_workspace/archive"
                overrides.setdefault("days", 90)
                overrides["group_by"] = None
            elif "recycle" in chosen or "bin" in chosen or "trash" in chosen:
                intent_override = "FILE_DELETE"   # the no-target guard will ask WHAT
            else:
                problem = "pick one of: " + ", ".join(clar.options)

        if problem:
            again = Turn(instruction=pending.instruction, status="clarified",
                         clarification=clar, slots=pending.slots, intent=pending.intent,
                         slot_overrides=overrides, intent_override=intent_override,
                         rounds=pending.rounds + 1)
            again.message = f"I could not use that answer: {problem}. {clar.question}"
            return again

        nxt = self.handle(pending.instruction, slot_overrides=overrides,
                          intent_override=intent_override, preview_only=preview_only)
        nxt.rounds = pending.rounds + 1
        return nxt

    def _interpret_answer(self, slot: str, answer: str):
        """Turn a free-text answer into a slot value. Returns (value, problem)."""
        from maestro.nlp.entities import FILE_TYPES

        low = answer.lower().strip(" .")
        if slot in ("source", "destination"):
            found = self.extractor.slots(answer)
            path = found.destination or found.source
            if path is None and ("/" in answer or "\\" in answer or answer.startswith("~")):
                path = answer.replace("\\", "/")
            if path is None:
                return None, ("I need a folder, for example ~/Documents/Invoices, "
                              "or a name like Downloads or the archive folder")
            return path, None
        if slot == "file_type":
            if "older" in low or "year" in low or "month" in low:
                return {"days": 365 if "year" in low else 30}, None
            for word in sorted(FILE_TYPES, key=len, reverse=True):
                if word in low:
                    return FILE_TYPES[word], None
            return None, ("name a file type (pdfs, screenshots, zip files) or "
                          "'files older than a year'")
        if slot == "app":
            found = self.extractor.slots(answer)
            value = found.app or (low or None)
            return value, (None if value else "which application?")
        if slot == "url":
            found = self.extractor.slots(answer)
            return found.url, (None if found.url else "I need a web address")
        if slot == "metric":
            found = self.extractor.slots(answer)
            return found.metric, (None if found.metric else
                                  "disk, memory, battery, cpu, or os?")
        if slot == "setting_value":
            found = self.extractor.slots(answer)
            return found.setting_value, (None if found.setting_value else
                                         "a number, like 30%")
        if slot == "setting_key":
            found = self.extractor.slots(answer)
            return found.setting_key or "volume", None
        if slot == "subject":
            return answer, None
        return (answer or None), (None if answer else "I did not catch that")

    def _cancelled(self, pending: Turn, message: str) -> Turn:
        t = Turn(instruction=pending.instruction, status="cancelled", message=message,
                 slots=pending.slots, intent=pending.intent, rounds=pending.rounds + 1)
        self._audit("ABORTED", pending.episode_id or None, detail="user cancelled")
        return t

    # --------------------------------------------------------- summarising --

    def summarize_file(self, path: str | Path) -> tuple[str, bool]:
        """Read a file and summarise it — the injection demo path.

        Note what does NOT happen here: the file's text never reaches the
        planner. It goes from the executor straight to the tool-less Summarizer.
        There is no code path from this method to an Action.
        """
        from maestro.executor.base import Context, get_executor

        verdict = self.policy.check(path)
        if verdict.name == "DENIED":
            return f"Refused: {self.policy.explain(path)}", False

        res = get_executor("fs.read_text").execute({"path": str(path)}, Context())
        if not res.ok:
            return f"Could not read {path}: {res.detail}", False

        summary = self.summarizer.summarize(res.output, what=Path(path).name)
        if summary.scan.detected:
            self._audit("INJECTION_DETECTED", None,
                        detail=f"{path}: {summary.scan.summary}")
        return summary.text, summary.scan.detected

    # ------------------------------------------------------------ internals --

    def _attach_exemplars(self, instruction: str, intent: str) -> None:
        """Retrieval-augmented planning (FR-51, ablation A4)."""
        llm = getattr(self.planner, "llm", None)
        if llm is None:
            return
        if not self.enable_memory or self.vectors is None:
            llm.exemplars = None
            return
        try:
            hits = self.vectors.query(instruction, k=3, intent=None)
        except Exception:
            hits = []
        if not hits and self.episodes is not None:
            pairs = self.episodes.successful_exemplars(intent, limit=3)
            llm.exemplars = pairs or None
            return
        llm.exemplars = [(h.instruction, h.plan) for h in hits] or None

    def _learn(self, instruction: str, plan: Plan, intent: str) -> None:
        try:
            if self.prefs is not None:
                self.prefs.learn_from_plan(instruction, plan)
                self.extractor.known_paths = {k.lower(): v
                                              for k, v in self.prefs.known_paths().items()}
            if self.vectors is not None:
                self.vectors.add(plan.plan_id, instruction,
                                 json.loads(plan.model_dump_json()), intent)
        except Exception:
            # Learning is best-effort. A memory write must never fail a task the
            # user already approved and MAESTRO already completed.
            pass

    def _record(self, turn: Turn, status: str, eid: str, pred: IntentPrediction,
                slots: Slots) -> None:
        if self.episodes is None:
            return
        report = turn.report
        self.episodes.record(
            episode_id=eid,
            instruction=turn.instruction,
            status=status,
            intent=pred.intent,
            intent_conf=pred.confidence,
            slots=slots.as_dict() if slots else None,
            plan_json=turn.plan.model_dump_json() if turn.plan else None,
            plan_risk=str(report.risk) if report and report.verdict else None,
            gate=report.gate if report and report.verdict else None,
            consent=report.approval.method if report and report.approval else None,
            steps_ok=report.steps_ok if report else 0,
            steps_total=len(report.steps) if report else 0,
            plan_ms=turn.plan_ms,
            exec_ms=report.exec_ms if report else 0.0,
            detail=turn.message[:500],
            planner=getattr(self.planner, "model", ""),
            strategy=turn.strategy,
            platform=_platform.system(),
            critic=report.critic.as_dicts() if report and report.critic else None,
            # The bound variables — the move manifests above all — are what a
            # later `maestro undo` needs. Persisted only for completed runs;
            # a rolled-back run has already been reversed.
            variables=(report.variables if report and report.status == "completed"
                       else None),
        )

    def _audit(self, event: str, eid: str | None, **kw) -> None:
        if self.audit:
            self.audit.append(event, episode_id=eid, **kw)

    def close(self) -> None:
        for obj in (self.audit, self.episodes, self.prefs, self.vectors):
            try:
                obj.close()  # type: ignore[union-attr]
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _outcome_message(report: RunReport) -> str:
    if report.status == "completed":
        n = sum(s.files_touched for s in report.steps)
        base = f"Done — {report.steps_ok} step(s) completed"
        return base + (f", {n} file(s) touched." if n else ".")
    if report.status == "blocked":
        return f"Refused. {report.message}"
    if report.status == "denied":
        return "Cancelled — nothing was changed."
    if report.status == "rolled_back":
        return f"A step failed, so I rolled the plan back. {report.message}"
    if report.status == "previewed":
        return report.message
    if report.status == "budget_exceeded":
        return f"Stopped and rolled back: {report.message}"
    return report.message or report.status


def _match_option(answer: str, options: list[str]) -> str | None:
    """Accept a number ('2'), the option text, or a distinctive word from it."""
    low = answer.lower().strip(" .")
    if not options:
        return None
    if low.isdigit() and 1 <= int(low) <= len(options):
        return options[int(low) - 1]
    for opt in options:
        if low == opt.lower() or low in opt.lower():
            return opt
    for opt in options:
        words = [w for w in opt.lower().split() if len(w) > 3]
        if any(w in low for w in words):
            return opt
    return None


def auto_approve(_: ConsentRequest) -> Approval:
    """Consent callback for automated runs. NEVER used for R3: the gate rejects
    a click on a typed_confirm plan, so a harness cannot accidentally approve
    something a human would have had to type a token for."""
    return Approval(True, "click")


def typed_approve(req: ConsentRequest) -> Approval:
    """Harness callback that also satisfies R3 gates (adversarial runs where we
    deliberately test what happens *if* the user approves)."""
    return Approval(True, "typed" if req.gate == "typed_confirm" else "click")


def always_deny(_: ConsentRequest) -> Approval:
    return Approval(False, "denied", note="denied by policy in this run")


def _version() -> str:
    from maestro import __version__

    return __version__
