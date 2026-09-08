"""The automated benchmark runner (docs/07 §6).

    python eval/harness.py                       # default config, both suites
    python eval/harness.py --configs B3 A1 A3    # named baselines / ablations
    python eval/harness.py --seeds 3             # repeat for mean +/- std
    python eval/harness.py --suite adversarial   # attacks only
    python eval/harness.py --list                # what configs exist

Every task runs inside a hermetic sandbox (`eval/fixtures.py`), through the same
`MaestroPipeline.handle()` that the CLI calls. A harness that reimplemented the
lifecycle would be measuring a system nobody runs.

Raw per-run records go to `eval/results/raw/<run_id>.jsonl` so that any number
in the report can be traced back to the run that produced it (docs/07 §6). The
aggregated metrics go to `eval/results/<run_id>.json`, and `eval/report.py`
turns those into the tables.

**Consent in an automated run.** The harness supplies `auto_approve`, which
clicks. It deliberately cannot satisfy an R3 typed_confirm gate — the consent
gate rejects a click there — so a harness run can never accidentally approve
something a human would have had to type a token for. R3 tasks therefore show
up as `denied`, which is the correct and honest outcome for an unattended run.
"""

from __future__ import annotations

import argparse
import json
import platform as _platform
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import maestro.executor  # noqa: F401,E402
from eval import predicates as pred_mod  # noqa: E402
from eval.fixtures import Sandbox, sandbox  # noqa: E402
from eval.metrics import (  # noqa: E402
    action_f1,
    adversarial_metrics,
    capability_metrics,
    failure_taxonomy,
    mean,
    plan_exact_match,
    safety_metrics,
    stdev,
    step_efficiency,
    verb_sequence_accuracy,
)
from maestro.pipeline import MaestroPipeline, auto_approve  # noqa: E402
from maestro.safety import ConsentGate  # noqa: E402

TASKS = ROOT / "eval" / "tasks"
RESULTS = ROOT / "eval" / "results"


# --------------------------------------------------------------------------- #
# configurations: baselines (docs/07 §4) and ablations (docs/07 §5)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Config:
    key: str
    label: str
    description: str
    enable_safety: bool = True
    enable_dry_run: bool = True
    enable_postconditions: bool = True
    enable_critic: bool = True
    enable_memory: bool = True
    use_trained_intent: bool = True
    enable_prefilter: bool = True
    force_rule_planner: bool = False
    force_llm_planner: bool = False

    def pipeline(self, sb: Sandbox) -> MaestroPipeline:
        from maestro.llm import router
        from maestro.planner import HybridPlanner, Planner

        planner = None
        if self.force_rule_planner:
            planner = HybridPlanner(llm=None)
        elif self.force_llm_planner:
            backend = router.pick()
            if not backend.available:
                raise RuntimeError(
                    f"config {self.key} needs an LLM but none is reachable "
                    f"({router.describe()})"
                )
            planner = HybridPlanner(llm=Planner(backend.client))

        return MaestroPipeline(
            policy=sb.policy,
            gate=ConsentGate(ask=auto_approve),
            planner=planner,
            enable_safety=self.enable_safety,
            enable_dry_run=self.enable_dry_run,
            enable_postconditions=self.enable_postconditions,
            enable_critic=self.enable_critic,
            enable_memory=self.enable_memory,
            use_trained_intent=self.use_trained_intent,
            enable_prefilter=self.enable_prefilter,
        )


CONFIGS: dict[str, Config] = {
    # -- baselines, docs/07 §4 --------------------------------------------
    "B0": Config("B0", "rule-based floor",
                 "deterministic templates only, full safety layer. Proves the "
                 "task is not trivial — whatever this solves, the LLM gets no "
                 "credit for.", force_rule_planner=True),
    "B1": Config("B1", "no safety layer",
                 "the naive agent everyone builds: plan and execute, no risk "
                 "scoring, no gate, no dry run, no postconditions. The gap "
                 "between B1's UER and B3's IS the result.",
                 enable_safety=False, enable_dry_run=False,
                 enable_postconditions=False, enable_critic=False,
                 enable_prefilter=False),
    "B2": Config("B2", "multi-agent, no safety layer",
                 "critic and memory on, safety layer off. Isolates the safety "
                 "layer from the multi-agent structure.",
                 enable_safety=False, enable_dry_run=False,
                 enable_postconditions=False, enable_prefilter=False),
    "B3": Config("B3", "MAESTRO (full)", "everything on. Ours."),
    "B4": Config("B4", "MAESTRO + LLM planner",
                 "full safety layer with the LLM planner forced on — the "
                 "capability ceiling of this architecture.",
                 force_llm_planner=True),
    # -- ablations, docs/07 §5 --------------------------------------------
    "A0": Config("A0", "full (reference)", "identical to B3; the ablation baseline"),
    "A1": Config("A1", "-safety layer", "RQ2: the price of safety",
                 enable_safety=False),
    "A2": Config("A2", "-dry run", "value of the preview", enable_dry_run=False),
    "A3": Config("A3", "-postconditions",
                 "silent-failure rate; expect a big jump (threat T8)",
                 enable_postconditions=False),
    "A4": Config("A4", "-memory/retrieval", "personalisation benefit",
                 enable_memory=False),
    "A5": Config("A5", "-critic", "over-reach rate", enable_critic=False),
    "A6": Config("A6", "-trained planner",
                 "RQ4: rule templates instead of the LLM planner",
                 force_rule_planner=True),
    "A7": Config("A7", "-trained intent model",
                 "staged NLP with the rule classifier instead of the trained one",
                 use_trained_intent=False),
    "A8": Config("A8", "-trust isolation",
                 "RQ3: injection defence value. NOTE: MAESTRO cannot actually "
                 "run without trust isolation — the Summarizer has no tools "
                 "*structurally*. This config disables the safety layer and the "
                 "taint-driven escalation, which is as close as the architecture "
                 "permits, and the report must say so rather than implying a "
                 "clean ablation.",
                 enable_safety=False, enable_critic=False,
                 enable_prefilter=False),
}

DEFAULT_CONFIGS = ["B3"]


# --------------------------------------------------------------------------- #
# capability detection
# --------------------------------------------------------------------------- #


def available_capabilities() -> set[str]:
    caps: set[str] = set()
    try:
        import playwright  # noqa: F401

        caps.add("browser")
    except ImportError:
        pass
    try:
        import psutil  # noqa: F401

        caps.add("psutil")
    except ImportError:
        pass
    if _has_network():
        caps.add("network")
    if _has_desktop():
        caps.add("desktop")
    return caps


def _has_network() -> bool:
    import socket

    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1.5).close()
        return True
    except OSError:
        return False


def _has_desktop() -> bool:
    """Are the app / volume tasks allowed to run?

    Opt-in, not auto-detected. `app.launch` genuinely opens the user's browser
    and `app.quit` genuinely kills it — a benchmark that does that to whoever
    runs it is not hermetic, and killing a running Chrome mid-run would be a
    real side effect on real data. Everything else in the suite is sandboxed;
    these twelve tasks cannot be, so they skip unless explicitly enabled with
    `--allow-desktop` (or MAESTRO_EVAL_DESKTOP=1).

    On a dedicated evaluation machine, turn it on. The skip count is reported
    either way, so the number is never quietly missing.
    """
    import os

    if os.environ.get("MAESTRO_EVAL_DESKTOP") not in ("1", "true", "yes"):
        return False
    if _platform.system() in ("Windows", "Darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# --------------------------------------------------------------------------- #
# running one task
# --------------------------------------------------------------------------- #


@dataclass
class RunContext:
    config: Config
    seed: int
    caps: set[str]
    run_id: str
    raw: list[dict] = field(default_factory=list)


def run_task(task: dict, ctx: RunContext) -> dict:
    tid = task.get("task_id", "?")
    missing = [c for c in task.get("requires", []) if c not in ctx.caps]
    base = {
        "run_id": ctx.run_id,
        "config": ctx.config.key,
        "seed": ctx.seed,
        "task_id": tid,
        "category": task.get("category") or task.get("group"),
        "group": task.get("group"),
        "difficulty": task.get("difficulty", "hard"),
        "instruction": task["instruction"],
        "expected_risk": task.get("expected_risk"),
        "expected_gate": task.get("expected_gate"),
        "expected_control": task.get("expected_control"),
        "platform": _platform.system(),
    }
    if missing:
        return {**base, "skipped": True, "skip_reason": f"requires {missing}",
                "success": False}

    expects_clarify = any(s["check"] == "asked_a_question"
                          for s in task.get("success", []))

    with sandbox(task.get("fixture")) as sb:
        before = sb.snapshot()
        t0 = time.perf_counter()
        pipe = None
        try:
            pipe = ctx.config.pipeline(sb)
            turn = pipe.handle(task["instruction"])
            elapsed = (time.perf_counter() - t0) * 1000

            plan_json = (json.loads(turn.plan.model_dump_json()) if turn.plan else None)
            gold = task.get("gold_plan")
            report = turn.report

            pctx = {
                "status": turn.status,
                "gate": turn.gate,
                "risk": turn.risk,
                "clarified": turn.status == "clarified",
                "message": turn.message,
                "files_before": before,
                "verbs": turn.plan.verb_sequence() if turn.plan else [],
            }
            results = pred_mod.evaluate(sb, task.get("success", []), pctx)
            full, partial, detail = pred_mod.summarize(results)

            audit_ok = pipe.audit.verify() if pipe.audit else None
            control = _control_fired(turn, report, task.get("group"))

            record = {
                **base,
                "skipped": False,
                "status": turn.status,
                "success": full,
                "partial": partial,
                "detail": detail,
                "message": turn.message[:300],
                "intent": turn.intent,
                "intent_confidence": round(turn.intent_confidence, 4),
                "clarified": turn.status == "clarified",
                "expects_clarify": expects_clarify,
                "risk": turn.risk,
                "gate": turn.gate,
                "consent_method": (report.approval.method
                                   if report and report.approval else None),
                "strategy": turn.strategy,
                "verbs": turn.plan.verb_sequence() if turn.plan else [],
                "n_actions": len(turn.plan.actions) if turn.plan else 0,
                "steps_ok": report.steps_ok if report else 0,
                "steps_total": len(report.steps) if report else 0,
                "critic_findings": (len(report.critic.findings)
                                    if report and report.critic else 0),
                "critic_serious": (len(report.critic.serious)
                                   if report and report.critic else 0),
                "plan_ms": round(turn.plan_ms, 1),
                "total_ms": round(elapsed, 1),
                "audit_verified": audit_ok,
                "control_fired": control,
                "predicates": [{"check": r.name, "ok": r.ok, "detail": r.detail}
                               for r in results],
            }
            if gold:
                record["plan_exact_match"] = plan_exact_match(plan_json, gold)
                record["action_f1"] = action_f1(plan_json, gold)["f1"]
                record["verb_seq_ok"] = verb_sequence_accuracy(plan_json, gold)
                record["step_efficiency"] = step_efficiency(plan_json, gold)

            # Undo reliability (URR): for a completed, undoable plan, reverse it
            # and check the filesystem really came back.
            if full and turn.plan and report and report.status == "completed" \
                    and any(a.undo for a in turn.plan.actions):
                undo = pipe.orchestrator.undo_run(turn.plan, report)
                after_undo = sb.snapshot()
                record["undo_attempted"] = True
                record["undo_steps"] = undo.reversed
                record["undo_skipped"] = undo.skipped
                # Dict equality: same paths AND same sha256 per path. Set
                # equality on filenames — the first version — could not tell a
                # restored file from a same-named file with different bytes.
                record["undo_restored"] = (after_undo == before)
                record["undo_verified_by"] = "sha256"
            return record

        except Exception as e:
            return {**base, "skipped": False, "status": "error", "success": False,
                    "partial": False, "expects_clarify": expects_clarify,
                    "detail": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc()[-800:],
                    "total_ms": round((time.perf_counter() - t0) * 1000, 1)}
        finally:
            if pipe is not None:
                pipe.close()


def _control_fired(turn: Any, report: Any, group: str | None = None) -> str | None:
    """Which defence stopped this, for the docs/06 §6 attribution table."""
    if turn.status == "refused":
        return "PREFILTER"
    # An injection case that ran a read-only plan to completion was defeated by
    # ARCHITECTURE, not by a rule: the payload never reached the planner (C1),
    # and the only component that read it has no tools (C2). No scorer rule
    # fires because nothing needed stopping — reporting that as "unattributed"
    # hid the two controls the whole design rests on.
    if group and group.startswith("injection") and turn.plan is not None             and turn.status in ("completed", "rolled_back"):
        read_only = {"fs.glob", "fs.list_dir", "fs.stat", "fs.read_text",
                     "search.by_name", "search.by_content", "search.recent",
                     "browser.open", "browser.extract", "sys.info"}
        if set(turn.plan.verb_sequence()) <= read_only:
            return "C1_C2_isolation"
    if turn.status == "clarified":
        return "CLARIFY"
    if report is None or report.verdict is None:
        return None
    if report.status == "blocked":
        return report.verdict.first_control or "C4_closed_registry"
    if report.status == "denied":
        return "GATE"
    if report.status == "budget_exceeded":
        return "BUDGET"
    return report.verdict.first_control


# --------------------------------------------------------------------------- #
# running a suite
# --------------------------------------------------------------------------- #


def load_suite(name: str) -> list[dict]:
    path = {"capability": TASKS / "benchmark_100.jsonl",
            "adversarial": TASKS / "adversarial_40.jsonl"}[name]
    if not path.exists():
        raise SystemExit(f"{path} is missing — run `python eval/build_benchmark.py`")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def run_config(config: Config, suites: list[str], seeds: int, caps: set[str],
               run_id: str, limit: int | None, verbose: bool) -> dict:
    out: dict[str, Any] = {"config": config.key, "label": config.label,
                           "description": config.description, "suites": {}}
    for suite in suites:
        tasks = load_suite(suite)
        if limit:
            tasks = tasks[:limit]
        per_seed: list[list[dict]] = []
        for seed in range(seeds):
            ctx = RunContext(config, seed, caps, run_id)
            records = []
            for i, task in enumerate(tasks, start=1):
                rec = run_task(task, ctx)
                records.append(rec)
                if verbose:
                    mark = ("skip" if rec.get("skipped")
                            else " ok " if rec.get("success") else "FAIL")
                    print(f"    [{mark}] {rec['task_id']:12s} {rec.get('status', ''):12s} "
                          f"{rec['instruction'][:46]}")
                elif i % 10 == 0:
                    print(f"    ... {i}/{len(tasks)}", flush=True)
            per_seed.append(records)

        flat = [r for rs in per_seed for r in rs]
        audit_flags = [r.get("audit_verified") for r in flat
                       if r.get("audit_verified") is not None]
        audit_ok = all(audit_flags) if audit_flags else None

        block: dict[str, Any] = {
            "n_tasks": len(tasks),
            "seeds": seeds,
            "skipped": sum(1 for r in per_seed[0] if r.get("skipped")),
            "raw": flat,
        }
        if suite == "capability":
            block["capability"] = capability_metrics(flat).as_dict()
            block["safety"] = safety_metrics(flat, audit_ok=audit_ok).as_dict()
            block["failures"] = failure_taxonomy(flat)
            if seeds > 1:
                tsrs = [capability_metrics(rs)["TSR"] for rs in per_seed]
                block["TSR_mean"] = round(mean(tsrs), 2)
                block["TSR_std"] = round(stdev(tsrs), 2)
            block["by_category"] = _breakdown(flat, "category")
            block["by_difficulty"] = _breakdown(flat, "difficulty")
        else:
            block["adversarial"] = adversarial_metrics(flat).as_dict()
            block["safety"] = safety_metrics(flat, audit_ok=audit_ok).as_dict()
        out["suites"][suite] = block
    return out


def _breakdown(runs: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in runs:
        groups.setdefault(str(r.get(key)), []).append(r)
    return {k: capability_metrics(v, k).as_dict() for k, v in sorted(groups.items())}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(description="MAESTRO evaluation harness")
    ap.add_argument("--configs", nargs="*", default=DEFAULT_CONFIGS)
    ap.add_argument("--suite", choices=["capability", "adversarial", "both"],
                    default="both")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None,
                    help="run only the first N tasks (smoke testing)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--allow-desktop", action="store_true",
                    help="run the app.launch / app.quit / volume tasks. They open "
                         "and close REAL applications on this machine and cannot "
                         "be sandboxed, so they are skipped by default.")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--list", action="store_true", help="list configs and exit")
    args = ap.parse_args()

    if args.list:
        print("configs:")
        for c in CONFIGS.values():
            print(f"  {c.key:4s} {c.label:26s} {c.description}")
        return 0

    unknown = [c for c in args.configs if c not in CONFIGS]
    if unknown:
        print(f"unknown config(s): {unknown}. Try --list")
        return 2

    suites = (["capability", "adversarial"] if args.suite == "both" else [args.suite])
    if args.allow_desktop:
        import os
        os.environ["MAESTRO_EVAL_DESKTOP"] = "1"
    caps = available_capabilities()
    run_id = args.out or f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

    print(f"MAESTRO evaluation · run {run_id}")
    print(f"  platform     {_platform.system()} {_platform.release()}")
    print(f"  capabilities {sorted(caps) or '(none — browser/desktop tasks skip)'}")
    print(f"  configs      {args.configs}")
    print(f"  suites       {suites} · seeds {args.seeds}")
    print()

    results = {
        "run_id": run_id,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": {"system": _platform.system(), "release": _platform.release(),
                     "python": _platform.python_version()},
        "capabilities": sorted(caps),
        "seeds": args.seeds,
        "configs": {},
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "raw").mkdir(parents=True, exist_ok=True)

    for key in args.configs:
        cfg = CONFIGS[key]
        print(f"[{key}] {cfg.label}")
        try:
            block = run_config(cfg, suites, args.seeds, caps, run_id, args.limit,
                               args.verbose)
        except RuntimeError as e:
            print(f"    skipped: {e}")
            results["configs"][key] = {"config": key, "skipped": str(e)}
            continue

        # Raw records to their own file; the summary keeps only the metrics.
        raw_path = RESULTS / "raw" / f"{run_id}_{key}.jsonl"
        with raw_path.open("w", encoding="utf-8") as f:
            for suite, blk in block["suites"].items():
                for rec in blk.pop("raw"):
                    rec["suite"] = suite
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        block["raw_file"] = str(raw_path.relative_to(ROOT))
        results["configs"][key] = block
        _print_config_summary(block)

    out_path = RESULTS / f"{run_id}.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print()
    print(f"results -> {out_path.relative_to(ROOT)}")
    print(f"tables  -> python eval/report.py {run_id}")
    return 0


def _print_config_summary(block: dict) -> None:
    cap = block["suites"].get("capability")
    if cap:
        c, s = cap["capability"], cap["safety"]
        print(f"    TSR {c.get('TSR')}%  PSR {c.get('PSR')}%  "
              f"skipped {cap['skipped']}  p50 {c.get('total_ms_p50')}ms")
        print(f"    UER {s.get('UER')}%  FCR {s.get('FCR')}%  "
              f"SCR {s.get('SCR')}%  RSA {s.get('RSA')}%  AIR {s.get('AIR')}%")
    adv = block["suites"].get("adversarial")
    if adv:
        a = adv["adversarial"]
        print(f"    IRR {a.get('IRR')}%  HBR {a.get('HBR')}%  "
              f"over-refusal {a.get('OverRefusalRate')}%")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
