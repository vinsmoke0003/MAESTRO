"""Cross-platform differential test (docs/02-ARCHITECTURE.md §7).

    python scripts/compare_platforms.py eval/results/darwin eval/results/win32
    python scripts/compare_platforms.py run_darwin.json run_win32.json

> "Run the same benchmark task on both platforms and assert the *Action IR is
>  identical* even though the executors differ. Any divergence means abstraction
>  leakage. This test is itself a nice contribution — most cross-platform agent
>  work never demonstrates behavioral equivalence, and you can."

What is compared, and why each one:

* **verb sequence** — if the planner emits different verbs on the two
  platforms, planner logic has learned about the OS. That is the leak the whole
  boundary exists to prevent, and it is a hard failure.
* **risk tier and gate** — the safety layer is platform-independent code, so a
  divergence here means the *path policy* resolved differently. Also a failure.
* **status** — allowed to differ. `app.launch` legitimately succeeds on a
  machine with Chrome and fails on one without, and that is a property of the
  machine, not of the architecture. Reported, never failed on.

Emits `cross_platform.md`, which is Table 7 of the report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_runs(target: Path) -> dict[str, dict]:
    """Read raw per-task records from a results directory or a run JSON."""
    records: dict[str, dict] = {}

    if target.is_file() and target.suffix == ".json":
        data = json.loads(target.read_text(encoding="utf-8"))
        raw_dir = target.parent / "raw"
        for cfg in data.get("configs", {}).values():
            name = cfg.get("raw_file")
            if not name:
                continue
            path = ROOT / name
            if not path.exists():
                path = raw_dir / Path(name).name
            if path.exists():
                records.update(_read_jsonl(path))
        return records

    for path in sorted(target.rglob("*.jsonl")):
        records.update(_read_jsonl(path))
    return records


def _read_jsonl(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("skipped"):
            continue
        out[f"{rec.get('config', '?')}::{rec['task_id']}"] = rec
    return out


def compare(left: dict[str, dict], right: dict[str, dict],
            left_name: str, right_name: str) -> tuple[list[dict], dict]:
    shared = sorted(set(left) & set(right))
    divergences: list[dict] = []
    identical_ir = 0

    for key in shared:
        a, b = left[key], right[key]
        problems = []
        if a.get("verbs") != b.get("verbs"):
            problems.append(("verb_sequence", a.get("verbs"), b.get("verbs")))
        if a.get("risk") != b.get("risk"):
            problems.append(("risk", a.get("risk"), b.get("risk")))
        if a.get("gate") != b.get("gate"):
            problems.append(("gate", a.get("gate"), b.get("gate")))

        if not problems:
            identical_ir += 1
        else:
            divergences.append({"task": key, "problems": problems,
                                "status": (a.get("status"), b.get("status"))})

    status_differs = [k for k in shared
                      if left[k].get("status") != right[k].get("status")]

    summary = {
        "left": left_name,
        "right": right_name,
        "compared": len(shared),
        "only_left": sorted(set(left) - set(right))[:10],
        "only_right": sorted(set(right) - set(left))[:10],
        "ir_identical": identical_ir,
        "ir_identity_rate": round(100 * identical_ir / len(shared), 2) if shared else 0.0,
        "status_differs": len(status_differs),
        "status_differs_examples": status_differs[:10],
    }
    return divergences, summary


TEMPLATE = """### Table 7 — Cross-platform equivalence

Comparing `{left}` against `{right}`.

| Metric | Value |
|---|---|
| Tasks compared (ran on both) | {compared} |
| **Action IR identical** | **{ir_identical} ({ir_identity_rate}%)** |
| Outcome differed | {status_differs} |

The IR-identity rate is the claim. Verb sequence, risk tier and gate are
produced by platform-independent code, so they must match exactly; only the
executors differ. Any divergence is abstraction leakage — planner or policy
logic that learned about the operating system — and is a bug rather than a
platform difference.

Outcome differences are expected and are not failures: `app.launch` succeeds on
a machine with Chrome installed and fails on one without. That is a property of
the machine, not of the architecture.

{divergence_section}
"""


def render(divergences: list[dict], summary: dict) -> str:
    if not divergences:
        section = ("**No divergences.** Every task produced a byte-identical "
                   "Action IR, risk tier and gate on both platforms.\n")
    else:
        lines = [f"**{len(divergences)} divergence(s) — investigate before "
                 f"reporting portability:**\n",
                 "| Task | Field | " + summary["left"] + " | " + summary["right"] + " |",
                 "|---|---|---|---|"]
        for d in divergences[:25]:
            for field, a, b in d["problems"]:
                lines.append(f"| {d['task']} | {field} | `{a}` | `{b}` |")
        section = "\n".join(lines) + "\n"
    return TEMPLATE.format(divergence_section=section, **summary)


def main() -> int:
    ap = argparse.ArgumentParser(description="cross-platform IR equivalence")
    ap.add_argument("left", help="results dir or run JSON from platform A")
    ap.add_argument("right", help="results dir or run JSON from platform B")
    ap.add_argument("--out", default="eval/results/cross_platform.md")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on any IR divergence")
    args = ap.parse_args()

    left_path, right_path = Path(args.left), Path(args.right)
    for p in (left_path, right_path):
        if not p.exists():
            print(f"missing: {p}")
            print("Run the harness on both platforms first, then point this at "
                  "the two results directories.")
            return 2

    left = load_runs(left_path)
    right = load_runs(right_path)
    if not left or not right:
        print(f"no run records found ({len(left)} vs {len(right)})")
        return 2

    divergences, summary = compare(left, right, left_path.name, right_path.name)
    report = render(divergences, summary)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"written to {out}")

    if divergences and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
