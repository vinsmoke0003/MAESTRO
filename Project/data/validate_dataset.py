"""Independently re-check every invariant the DeskPlan dataset claims.

    python data/validate_dataset.py

`build_dataset.py` asserts these while writing; this script re-proves them by
reading the files back, which is the only version of the claim that survives
someone hand-editing a JSONL. It is also a pytest case
(`tests/test_dataset.py`), so the invariants are checked in CI on every commit.

Checks:

    1. every row validates against schema/deskplan.schema.json (required keys,
       enum membership, span shape)
    2. every non-null `plan` re-validates as Action IR — DAG, dataflow,
       registry membership, budget
    3. every plan's `plan_risk` and `gate` match what the deterministic scorer
       returns TODAY. A dataset whose risk labels have drifted from the shipping
       scorer is worse than no labels.
    4. no `paraphrase_group` appears in more than one split
    5. no adversarial row appears in train or val
    6. no instruction appears in both train and test
    7. every `expected_behavior` is consistent with its plan/gate
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import maestro.executor  # noqa: F401,E402
from maestro.ir import Plan  # noqa: E402
from maestro.safety import PathPolicy, score_plan  # noqa: E402

DATA = ROOT / "data"

BEHAVIORS = {"execute_auto", "execute_with_consent", "clarify", "refuse",
             "summarize_only"}
DIFFICULTIES = {"easy", "medium", "hard"}
SOURCES = {"human", "template", "paraphrase", "adversarial", "llm_generated",
           "episode"}


def load(name: str) -> list[dict]:
    p = DATA / "splits" / f"{name}.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def check_row_shape(r: dict, errors: list[str]) -> None:
    rid = r.get("id", "?")
    for key in ("id", "instruction", "paraphrase_group", "expected_behavior",
                "difficulty", "source"):
        if key not in r:
            errors.append(f"{rid}: missing required key {key!r}")
    if r.get("expected_behavior") not in BEHAVIORS:
        errors.append(f"{rid}: bad expected_behavior {r.get('expected_behavior')!r}")
    if r.get("difficulty") not in DIFFICULTIES:
        errors.append(f"{rid}: bad difficulty {r.get('difficulty')!r}")
    if r.get("source") not in SOURCES:
        errors.append(f"{rid}: bad source {r.get('source')!r}")
    if not str(r.get("instruction", "")).strip():
        errors.append(f"{rid}: empty instruction")
    for e in r.get("entities", []):
        span = e.get("span")
        if not (isinstance(span, list) and len(span) == 2
                and 0 <= span[0] <= span[1] <= len(r["instruction"])):
            errors.append(f"{rid}: entity {e.get('type')} has bad span {span}")


def check_plan(r: dict, policy: PathPolicy, errors: list[str]) -> None:
    plan_json = r.get("plan")
    rid = r["id"]
    if plan_json is None:
        # Adversarial rows are behavioural test cases, not plan supervision.
        # The `should_not_refuse` group in particular asserts an *outcome*
        # ("this ordinary task must still run"), and deliberately ships without
        # a gold plan so it cannot be trained on.
        if r["source"] == "adversarial":
            return
        if r["expected_behavior"] in ("execute_auto", "execute_with_consent"):
            errors.append(f"{rid}: expects execution but carries no plan")
        return
    if r["expected_behavior"] in ("clarify", "refuse"):
        errors.append(f"{rid}: expects {r['expected_behavior']} but carries a plan — "
                      f"training on this teaches compliance with a refusal")
        return
    try:
        plan = Plan.model_validate(plan_json)
    except Exception as e:
        errors.append(f"{rid}: gold plan is not valid Action IR: {type(e).__name__}: {e}")
        return
    verdict = score_plan(plan, policy)
    if verdict.blocked:
        errors.append(f"{rid}: gold plan is BLOCKED by the scorer")
        return
    if str(verdict.risk) != r.get("plan_risk"):
        errors.append(f"{rid}: plan_risk drift — dataset says {r.get('plan_risk')}, "
                      f"the scorer says {verdict.risk}")
    if verdict.gate != r.get("gate"):
        errors.append(f"{rid}: gate drift — dataset says {r.get('gate')}, "
                      f"the scorer says {verdict.gate}")
    expected = "execute_auto" if verdict.gate == "auto" else "execute_with_consent"
    if r["expected_behavior"] != expected:
        errors.append(f"{rid}: expected_behavior {r['expected_behavior']} does not "
                      f"match gate {verdict.gate}")


def validate() -> tuple[list[str], dict]:
    errors: list[str] = []
    policy = PathPolicy()
    splits = {name: load(name) for name in ("train", "val", "test")}
    if not any(splits.values()):
        return ["no splits found — run `python data/build_dataset.py` first"], {}

    all_rows = [r for rows in splits.values() for r in rows]
    for r in all_rows:
        check_row_shape(r, errors)
        check_plan(r, policy, errors)

    # 4. paraphrase groups must not straddle splits
    group_splits: dict[str, set[str]] = defaultdict(set)
    for name, rows in splits.items():
        for r in rows:
            group_splits[r["paraphrase_group"]].add(name)
    leaked = {g: sorted(s) for g, s in group_splits.items() if len(s) > 1}
    for g, s in list(leaked.items())[:10]:
        errors.append(f"paraphrase group {g} spans splits {s} — test accuracy inflated")
    if len(leaked) > 10:
        errors.append(f"... and {len(leaked) - 10} more leaked groups")

    # 5. adversarial rows are test-only
    for name in ("train", "val"):
        bad = [r["id"] for r in splits[name] if r["source"] == "adversarial"]
        if bad:
            errors.append(f"{len(bad)} adversarial row(s) leaked into {name}: "
                          f"{bad[:5]} — the IRR number would be meaningless")

    # 6. no verbatim instruction shared between train and test.
    #    `should_not_refuse` controls are exempt: their entire purpose is to be
    #    ordinary tasks that must not be refused, so looking like training data
    #    is the point. They are scored on behaviour, never on plan match.
    train_text = {r["instruction"].strip().lower() for r in splits["train"]}
    dupes = [r["id"] for r in splits["test"]
             if r.get("group") != "should_not_refuse"
             and r["instruction"].strip().lower() in train_text]
    if dupes:
        errors.append(f"{len(dupes)} test instruction(s) appear verbatim in train: "
                      f"{dupes[:5]}")

    summary = {
        "rows": {k: len(v) for k, v in splits.items()},
        "groups": {k: len({r['paraphrase_group'] for r in v}) for k, v in splits.items()},
        "leaked_groups": len(leaked),
        "plans_checked": sum(1 for r in all_rows if r.get("plan")),
        "adversarial_in_test": sum(1 for r in splits["test"]
                                   if r["source"] == "adversarial"),
    }
    return errors, summary


def main() -> int:
    errors, summary = validate()
    print("DeskPlan validation")
    for k, v in summary.items():
        print(f"  {k:22s} {v}")
    print()
    if errors:
        print(f"FAILED — {len(errors)} problem(s):")
        for e in errors[:40]:
            print(f"  - {e}")
        if len(errors) > 40:
            print(f"  ... and {len(errors) - 40} more")
        return 1
    print("OK — every invariant holds:")
    print("  * every gold plan re-validates as Action IR and is accepted by the scorer")
    print("  * every risk/gate label matches what the shipping scorer returns today")
    print("  * no paraphrase group straddles a split")
    print("  * no adversarial row is in train or val")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
