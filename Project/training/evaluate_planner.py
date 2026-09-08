"""Evaluate a planner against the DeskPlan test split (docs/05 §3).

    python training/evaluate_planner.py                    # the rule baseline (M0)
    python training/evaluate_planner.py --model qwen2.5:7b-instruct-q4_K_M
    python training/evaluate_planner.py --model maestro-planner --limit 100
    python training/evaluate_planner.py --by-difficulty --by-verb

Metrics, all from docs/05 §3:

    Plan Exact Match       canonicalised plan == gold
    Action-level F1        set-F1 over (verb, canonical args)
    Verb accuracy          correct verb sequence, ignoring arg values
    Schema validity        parses and validates against the Action IR
    DAG validity           acyclic and fully resolvable
    Risk-hint agreement    planner's guess vs. the deterministic scorer
    Refusal accuracy       correct refuse/clarify on the refusal rows
    Latency p50 / p95      measured on this machine

**Risk-hint agreement is the interesting one.** It measures whether the model
*understands* risk. It is never trusted for anything — the deterministic scorer
overwrites it — so a model can score badly here and the system stays exactly as
safe. That is the point worth making in the report: the number is diagnostic,
not load-bearing.

**Refusal accuracy is measured on the model alone.** The deterministic prefilter
is bypassed for this evaluation, because otherwise every model scores 100% and
the column says nothing about the model. In production the prefilter runs first
and this number does not affect safety — which is precisely the argument for
having the prefilter.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import maestro.executor  # noqa: F401,E402
from eval.metrics import (  # noqa: E402
    action_f1,
    mean,
    percentile,
    plan_exact_match,
    verb_sequence_accuracy,  # noqa: E402
)
from maestro.nlp import EntityExtractor  # noqa: E402
from maestro.planner import PlannerError  # noqa: E402
from maestro.safety import PathPolicy, score_plan  # noqa: E402

TEST = ROOT / "data" / "splits" / "test.jsonl"
OUT = ROOT / "eval" / "results"


def load_test(limit: int | None, include_refusals: bool = True) -> list[dict]:
    rows = [json.loads(x) for x in TEST.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    rows = [r for r in rows if r.get("source") != "adversarial"]
    if not include_refusals:
        rows = [r for r in rows if r.get("expected_behavior", "").startswith("execute")]
    return rows[:limit] if limit else rows


def make_planner(model: str | None):
    """Rule baseline when --model is absent, otherwise the LLM planner."""
    from maestro.llm import router
    from maestro.planner import HybridPlanner, Planner

    if not model:
        return HybridPlanner(llm=None), "rule-based (M0 baseline)"

    import os

    os.environ["MAESTRO_MODEL"] = model
    backend = router.pick()
    if not backend.available:
        raise SystemExit(
            f"no LLM backend reachable for model {model!r}.\n"
            f"  {router.describe()}\n"
            f"Start one with `ollama serve && ollama pull {model}`, or omit "
            f"--model to evaluate the rule baseline."
        )
    # No fallback: this is an evaluation of the LLM, so a silent drop to the
    # rule planner would report the wrong system's numbers.
    return Planner(backend.client), f"{backend.name}:{model}"


def evaluate(model: str | None, limit: int | None) -> dict:
    planner, label = make_planner(model)
    extractor = EntityExtractor()
    policy = PathPolicy()
    rows = load_test(limit)

    print(f"planner: {label}")
    print(f"test rows: {len(rows)}")
    print()

    results: list[dict] = []
    latencies: list[float] = []

    for i, row in enumerate(rows, start=1):
        instruction = row["instruction"]
        behavior = row.get("expected_behavior")
        gold = row.get("plan")
        intent = row.get("intent")
        slots = extractor.slots(instruction, intent)

        t0 = time.perf_counter()
        plan, error = None, None
        try:
            plan = planner.plan(instruction, intent, slots)
        except PlannerError as e:
            error = str(e)[:160]
        except Exception as e:
            error = f"{type(e).__name__}: {e}"[:160]
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)

        rec: dict = {
            "id": row["id"], "instruction": instruction, "intent": intent,
            "difficulty": row.get("difficulty"), "expected_behavior": behavior,
            "ms": round(ms, 1), "error": error,
            "schema_valid": plan is not None,
            "dag_valid": plan is not None,  # Plan construction enforces both
        }

        if plan is not None:
            produced = json.loads(plan.model_dump_json())
            rec["verbs"] = plan.verb_sequence()
            if gold:
                rec["exact_match"] = plan_exact_match(produced, gold)
                rec["action_f1"] = action_f1(produced, gold)["f1"]
                rec["verb_seq_ok"] = verb_sequence_accuracy(produced, gold)
            verdict = score_plan(plan, policy)
            rec["risk"] = str(verdict.risk)
            hints = [a.hint_agrees for a in verdict.actions
                     if a.hint_agrees is not None]
            rec["hint_agreement"] = (sum(hints) / len(hints)) if hints else None
            # A planner that emits a plan for a row whose gold answer is "refuse"
            # or "clarify" got it wrong, whatever the plan looks like.
            rec["refusal_correct"] = behavior.startswith("execute")
        else:
            rec["refusal_correct"] = behavior in ("refuse", "clarify")

        results.append(rec)
        if i % 25 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    return summarize(label, model, results, latencies)


def summarize(label: str, model: str | None, results: list[dict],
              latencies: list[float]) -> dict:
    planned = [r for r in results if r["schema_valid"]]
    scored = [r for r in results if "exact_match" in r]
    refusal_rows = [r for r in results
                    if r["expected_behavior"] in ("refuse", "clarify")]
    hints = [r["hint_agreement"] for r in results
             if r.get("hint_agreement") is not None]

    def pct(n: int, d: int) -> float:
        return round(100 * n / d, 2) if d else 0.0

    summary = {
        "planner": label,
        "model": model or "rule-based",
        "n": len(results),
        "PlanExactMatch": pct(sum(1 for r in scored if r["exact_match"]), len(scored)),
        "ActionF1": round(mean([r["action_f1"] for r in scored]), 4) if scored else None,
        "VerbAccuracy": pct(sum(1 for r in scored if r["verb_seq_ok"]), len(scored)),
        "SchemaValidity": pct(len(planned), len(results)),
        "DagValidity": pct(sum(1 for r in planned if r["dag_valid"]), len(planned)),
        "RiskHintAgreement": round(100 * mean(hints), 2) if hints else None,
        "RefusalAccuracy": pct(sum(1 for r in refusal_rows if r["refusal_correct"]),
                               len(refusal_rows)),
        "RefusalAccuracy_n": len(refusal_rows),
        "latency_p50_ms": round(percentile(latencies, 50), 1),
        "latency_p95_ms": round(percentile(latencies, 95), 1),
        "n_scored_against_gold": len(scored),
    }

    by_diff: dict[str, dict] = {}
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in scored:
        groups[r["difficulty"] or "?"].append(r)
    for k, rs in sorted(groups.items()):
        by_diff[k] = {"n": len(rs),
                      "exact_match": pct(sum(1 for r in rs if r["exact_match"]), len(rs)),
                      "action_f1": round(mean([r["action_f1"] for r in rs]), 4)}
    summary["by_difficulty"] = by_diff

    verb_errors: Counter = Counter()
    for r in scored:
        if not r["verb_seq_ok"]:
            verb_errors[" -> ".join(r.get("verbs", [])) or "(empty)"] += 1
    summary["worst_verb_sequences"] = dict(verb_errors.most_common(8))
    summary["failures"] = [
        {"id": r["id"], "instruction": r["instruction"][:70],
         "error": r["error"] or "wrong plan"}
        for r in results if not r["schema_valid"] or
        (("exact_match" in r) and not r["exact_match"])
    ][:15]
    return summary


def print_summary(s: dict, by_difficulty: bool, by_verb: bool) -> None:
    print()
    print("=" * 68)
    print(f"planner: {s['planner']}   n={s['n']}")
    print("=" * 68)
    for key in ("PlanExactMatch", "ActionF1", "VerbAccuracy", "SchemaValidity",
                "DagValidity", "RiskHintAgreement", "RefusalAccuracy",
                "latency_p50_ms", "latency_p95_ms"):
        value = s.get(key)
        suffix = ""
        if key == "RefusalAccuracy":
            suffix = f"  (n={s['RefusalAccuracy_n']}, prefilter bypassed)"
        if key == "RiskHintAgreement":
            suffix = "  (diagnostic only — the scorer overwrites it)"
        print(f"  {key:20s} {value}{suffix}")

    if by_difficulty and s.get("by_difficulty"):
        print()
        print("  by difficulty:")
        for k, v in s["by_difficulty"].items():
            print(f"    {k:8s} n={v['n']:4d}  exact {v['exact_match']:6.2f}%  "
                  f"F1 {v['action_f1']}")

    if by_verb and s.get("worst_verb_sequences"):
        print()
        print("  most common wrong verb sequences:")
        for seq, n in s["worst_verb_sequences"].items():
            print(f"    {n:3d}  {seq}")

    if s.get("failures"):
        print()
        print(f"  first {len(s['failures'])} failures:")
        for f in s["failures"][:8]:
            print(f"    {f['id']}: {f['instruction']}")
            print(f"        {f['error']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="evaluate a planner on DeskPlan test")
    ap.add_argument("--model", default=None,
                    help="Ollama model id; omit for the rule baseline")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--by-difficulty", action="store_true")
    ap.add_argument("--by-verb", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    summary = evaluate(args.model, args.limit)
    print_summary(summary, args.by_difficulty, args.by_verb)

    OUT.mkdir(parents=True, exist_ok=True)
    name = args.out or f"planner_{(args.model or 'rule').replace(':', '_').replace('/', '_')}.json"
    path = OUT / name
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print()
    print(f"written to {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
