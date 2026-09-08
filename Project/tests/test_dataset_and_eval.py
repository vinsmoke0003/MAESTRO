"""The dataset's invariants and the evaluation machinery.

Running `data/validate_dataset.py` as a test matters more than it looks: the
invariants it checks (no group straddles a split, no adversarial row in train,
every risk label matches the shipping scorer) are properties that decay
silently. A hand-edited JSONL or a change to the scorer's rules breaks them
without breaking anything that would otherwise fail, and the numbers in the
report quietly stop meaning what they say.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import maestro.executor  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
SPLITS = ROOT / "data" / "splits"
TASKS = ROOT / "eval" / "tasks"

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


requires_dataset = pytest.mark.skipif(
    not (SPLITS / "train.jsonl").exists(),
    reason="dataset not built — run `python data/build_dataset.py`")

requires_benchmark = pytest.mark.skipif(
    not (TASKS / "benchmark_100.jsonl").exists(),
    reason="benchmark not built — run `python eval/build_benchmark.py`")


# =========================================================================== #
# the dataset
# =========================================================================== #


@requires_dataset
def test_dataset_validator_passes():
    """The whole of `data/validate_dataset.py`, as one test."""
    sys.path.insert(0, str(ROOT))
    from data.validate_dataset import validate

    errors, summary = validate()
    assert not errors, "\n".join(errors[:10])
    assert summary["leaked_groups"] == 0


@requires_dataset
def test_dataset_is_large_enough_to_report_on():
    total = sum(len(_load(SPLITS / f"{s}.jsonl")) for s in ("train", "val", "test"))
    assert total >= 3000, f"only {total} rows; docs/05 §2 targets 3,000+"


@requires_dataset
def test_no_paraphrase_group_straddles_a_split():
    seen: dict[str, str] = {}
    for split in ("train", "val", "test"):
        for row in _load(SPLITS / f"{split}.jsonl"):
            group = row["paraphrase_group"]
            if group in seen and seen[group] != split:
                pytest.fail(f"group {group} is in both {seen[group]} and {split}")
            seen[group] = split


@requires_dataset
def test_adversarial_rows_are_test_only():
    """Training on the evaluation attacks would make IRR meaningless."""
    for split in ("train", "val"):
        leaked = [r["id"] for r in _load(SPLITS / f"{split}.jsonl")
                  if r["source"] == "adversarial"]
        assert not leaked, f"{len(leaked)} adversarial rows in {split}"


@requires_dataset
def test_refusal_rows_carry_no_plan():
    """A refusal exported with the plan that was attempted teaches compliance."""
    for split in ("train", "val", "test"):
        for row in _load(SPLITS / f"{split}.jsonl"):
            if row.get("expected_behavior") in ("refuse", "clarify"):
                assert row.get("plan") is None, row["id"]


@requires_dataset
def test_every_gold_plan_still_validates_and_scores():
    """The dataset's risk labels must match what the scorer returns TODAY.

    If someone changes a scoring rule, this fails — which is the point. A
    dataset whose labels have drifted from the shipping system is worse than
    one with no labels, because it looks authoritative.
    """
    from maestro.ir import Plan
    from maestro.safety import PathPolicy, score_plan

    policy = PathPolicy()
    checked = 0
    for split in ("train", "val", "test"):
        for row in _load(SPLITS / f"{split}.jsonl"):
            if not row.get("plan"):
                continue
            plan = Plan.model_validate(row["plan"])
            verdict = score_plan(plan, policy)
            assert not verdict.blocked, row["id"]
            assert str(verdict.risk) == row["plan_risk"], row["id"]
            assert verdict.gate == row["gate"], row["id"]
            checked += 1
    assert checked > 1000


@requires_dataset
def test_unsafe_rows_are_actually_caught_by_the_prefilter():
    """A row labelled UNSAFE_REQUEST that the prefilter misses is a labelling
    lie: the shipping system would plan it, not refuse it."""
    from maestro.nlp.classifier import safety_prefilter

    for split in ("train", "val", "test"):
        for row in _load(SPLITS / f"{split}.jsonl"):
            if row.get("intent") != "UNSAFE_REQUEST":
                continue
            hit = safety_prefilter(row["instruction"])
            assert hit and hit[0] == "UNSAFE_REQUEST", row["instruction"]


@requires_dataset
def test_all_intents_have_test_support():
    """Per-class F1 on n=0 is not a number. docs/05 §1 asks for OUT_OF_SCOPE and
    UNSAFE_REQUEST in bold, which requires them to be in the test split at all."""
    from collections import Counter

    counts = Counter(r["intent"] for r in _load(SPLITS / "test.jsonl") if r.get("intent"))
    for intent in ("OUT_OF_SCOPE", "UNSAFE_REQUEST", "FILE_ORGANIZE", "SYSTEM_QUERY"):
        assert counts.get(intent, 0) >= 1, f"{intent} has no test rows"


@requires_dataset
def test_difficulty_distribution_is_not_all_easy():
    """"Too many easy pairs and the metrics look great and mean nothing"
    (docs/05 §2)."""
    from collections import Counter

    rows = [r for s in ("train", "val", "test") for r in _load(SPLITS / f"{s}.jsonl")]
    counts = Counter(r["difficulty"] for r in rows)
    assert counts["hard"] / len(rows) > 0.10
    assert counts["easy"] / len(rows) < 0.60


# =========================================================================== #
# the benchmark suites
# =========================================================================== #


@requires_benchmark
def test_benchmark_has_100_tasks_in_the_documented_distribution():
    from collections import Counter

    tasks = _load(TASKS / "benchmark_100.jsonl")
    assert len(tasks) == 100
    per_category = Counter(t["category"] for t in tasks)
    expected = {"T1": 24, "T2": 16, "T3": 20, "T4": 12, "T5": 10, "T6": 8, "T7": 10}
    assert dict(per_category) == expected


@requires_benchmark
def test_every_task_declares_success_predicates():
    for task in _load(TASKS / "benchmark_100.jsonl"):
        assert task["success"], task["task_id"]


@requires_benchmark
def test_every_predicate_name_is_known():
    """A typo in a task record would otherwise be a free pass."""
    from eval.predicates import PREDICATES

    for suite in ("benchmark_100.jsonl", "adversarial_40.jsonl"):
        for task in _load(TASKS / suite):
            for spec in task["success"]:
                assert spec["check"] in PREDICATES, f"{task['task_id']}: {spec}"


@requires_benchmark
def test_adversarial_suite_includes_must_not_refuse_controls():
    """Without them a system that refuses everything scores 100% IRR."""
    groups = {t["group"] for t in _load(TASKS / "adversarial_40.jsonl")}
    assert "should_not_refuse" in groups
    assert {"direct_unsafe", "injection_file", "injection_web", "scope_escape",
            "ambiguous"} <= groups


# =========================================================================== #
# metrics
# =========================================================================== #


def test_uer_is_measured_against_ground_truth_not_self_report():
    """The bug this pins: scored against its own verdict, a system with the
    safety layer switched off marks everything R0, finds no gate opportunities,
    and reports UER = 0 — i.e. removing all safety looks perfectly safe."""
    from eval.metrics import safety_metrics

    runs = [{
        "expected_risk": "R2",   # the benchmark says this deserved a gate
        "risk": "R0",            # the config under test disagreed
        "gate": "auto",
        "status": "completed",
        "consent_method": "auto",
    }]
    assert safety_metrics(runs)["UER"] == 100.0


def test_uer_is_zero_when_the_gate_was_honoured():
    from eval.metrics import safety_metrics

    runs = [{"expected_risk": "R2", "risk": "R2", "gate": "confirm",
             "status": "completed", "consent_method": "click"}]
    assert safety_metrics(runs)["UER"] == 0.0


def test_fcr_counts_prompts_on_genuinely_low_risk_tasks():
    from eval.metrics import safety_metrics

    runs = [{"expected_risk": "R0", "risk": "R2", "gate": "confirm",
             "status": "completed", "consent_method": "click"}]
    assert safety_metrics(runs)["FCR"] == 100.0


def test_uer_and_fcr_are_always_reported_together():
    """docs/07 §2: UER=0 achieved by prompting on everything is worthless."""
    from eval.metrics import safety_metrics

    values = safety_metrics([{"expected_risk": "R2", "risk": "R2", "gate": "confirm",
                              "status": "completed", "consent_method": "click"}]).values
    assert "UER" in values and "FCR" in values


def test_plan_exact_match_is_canonical():
    from eval.metrics import plan_exact_match

    a = {"actions": [{"verb": "fs.glob", "args": {"root": "~/Downloads/", "p": 1}}]}
    b = {"actions": [{"verb": "fs.glob", "args": {"p": 1, "root": "~/Downloads"}}]}
    assert plan_exact_match(a, b)


def test_action_f1_is_a_multiset():
    from eval.metrics import action_f1

    gold = {"actions": [{"verb": "fs.glob", "args": {}}]}
    doubled = {"actions": [{"verb": "fs.glob", "args": {}},
                           {"verb": "fs.glob", "args": {}}]}
    assert action_f1(doubled, gold)["f1"] < 1.0


def test_adversarial_metrics_separate_attacks_from_controls():
    from eval.metrics import adversarial_metrics

    runs = [
        {"group": "direct_unsafe", "success": True, "control_fired": "PREFILTER",
         "expected_control": "PREFILTER", "task_id": "a"},
        {"group": "direct_unsafe", "success": False, "task_id": "b"},
        {"group": "should_not_refuse", "success": False, "task_id": "c"},
    ]
    m = adversarial_metrics(runs)
    assert m["IRR"] == 50.0          # 1 of 2 attacks resisted
    assert m["OverRefusalRate"] == 100.0
    assert m["unresisted"] == ["b"]


def test_failure_taxonomy_classifies():
    from eval.metrics import failure_taxonomy

    runs = [
        {"status": "completed", "success": False, "expects_clarify": True,
         "clarified": False, "task_id": "t1", "instruction": "clean up"},
        {"status": "failed", "success": False, "task_id": "t2", "instruction": "x"},
    ]
    tax = failure_taxonomy(runs)
    assert "missing_clarification" in tax
    assert "planning_failed" in tax


def test_mcnemar_on_identical_systems_is_not_significant():
    from eval.metrics import mcnemar

    result = mcnemar([True, True, False], [True, True, False])
    assert result["p_value"] == 1.0


# =========================================================================== #
# the platform boundary (docs/02 §7)
# =========================================================================== #


def test_no_platform_branching_outside_the_executor_package():
    """The cross-platform strategy is architectural, so it needs a mechanical
    check. `maestro/executor/platform.py` is the single allowed exception."""
    script = ROOT / "scripts" / "check_platform_boundary.py"
    if not script.exists():
        pytest.skip("boundary checker not present")
    proc = subprocess.run([sys.executable, str(script)], capture_output=True,
                          text=True, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
