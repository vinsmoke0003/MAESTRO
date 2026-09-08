"""Turn DeskPlan into fine-tuning files (docs/05-NLP-AND-TRAINING.md §3).

    python training/prepare_data.py                # all formats
    python training/prepare_data.py --format mlx   # just the MLX layout

Writes:
    training/data/chatml/{train,valid,test}.jsonl   {"messages": [...]}  (Unsloth/TRL)
    training/data/mlx/{train,valid,test}.jsonl      {"text": "..."}      (mlx_lm.lora)
    training/data/stats.json                        token-length distribution

Three things this script gets right that are easy to get wrong:

**1. The prompt is built by the same code the planner uses at inference.**
`maestro.planner.prompts.training_prompt()` is imported here, not re-implemented.
docs/05 §3: "Keep it identical between training and inference. A format mismatch
here is the most common cause of 'the fine-tune made it worse', and it is
invisible unless you look for it."

**2. Refusals are trained as refusals.** A row whose `expected_behavior` is
`refuse` or `clarify` gets a *refusal or question* as its target, never the plan
that was attempted. Emitting the plan would train the model to comply with
exactly the requests it must decline — the labelling bug docs/05 §2 warns about,
and the one the v0.2 episode exporter actually had.

**3. It reports the token-length distribution before you fix max_seq_len.**
docs/05 §3 says to check p99 before setting 2048. If p99 exceeds the configured
length, training silently truncates the *end* of the target — which is the end
of the JSON plan — and every truncated example teaches the model to emit invalid
JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import maestro.executor  # noqa: F401,E402
from maestro.planner.prompts import training_prompt  # noqa: E402

SPLITS = {"train": "train", "val": "valid", "test": "test"}
OUT = ROOT / "training" / "data"

REFUSAL_TARGET = (
    "I can't do that. MAESTRO refuses this class of request outright and there is "
    "no override: permanent deletion, sending messages on your behalf, entering "
    "credentials, purchases, privilege escalation, disabling security controls and "
    "shell execution are hard-blocked. Reversible alternatives exist for most of "
    "them — I can move files to the Recycle Bin, or write an email draft for you to "
    "review and send yourself."
)
OUT_OF_SCOPE_TARGET = (
    "That is outside what MAESTRO does. I automate desktop tasks: files and folders, "
    "search, the browser, applications, system information, and drafting."
)


def load_split(name: str) -> list[dict]:
    path = ROOT / "data" / "splits" / f"{name}.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} is missing — run `python data/build_dataset.py`")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def target_for(row: dict) -> str | None:
    """What the model should emit. None means "do not train on this row"."""
    behavior = row.get("expected_behavior")

    if behavior in ("execute_auto", "execute_with_consent"):
        plan = row.get("plan")
        if not plan:
            return None
        # Only the fields the planner is asked to produce. Training on plan_id,
        # timestamps or the instruction hash would teach the model to invent
        # provenance that MAESTRO binds itself.
        actions = [{
            "action_id": a["action_id"],
            "verb": a["verb"],
            "args": a.get("args", {}),
            "depends_on": a.get("depends_on", []),
            "produces": a.get("produces"),
            "risk_hint": a.get("risk_hint"),
            "rationale": a.get("rationale", ""),
        } for a in plan.get("actions", [])]
        return json.dumps({"actions": actions}, ensure_ascii=False)

    if behavior == "refuse":
        return (OUT_OF_SCOPE_TARGET if row.get("intent") == "OUT_OF_SCOPE"
                else REFUSAL_TARGET)

    if behavior == "clarify":
        # The gold output is a question. Training the plan here would teach the
        # model to guess a destination it was never given.
        slots = row.get("slots") or {}
        missing = "where the files should go" if not slots.get("destination") \
            else "which folder you mean"
        return (f"I need one more detail before I can plan this: {missing}. "
                f"I will not guess a path.")

    return None


def build_rows(name: str) -> list[dict]:
    out = []
    for row in load_split(name):
        if row.get("source") == "adversarial":
            continue  # test-only, never a training target (docs/05 §2)
        target = target_for(row)
        if target is None:
            continue
        system, user = training_prompt(row["instruction"], row.get("context", {}))
        out.append({
            "id": row["id"],
            "system": system,
            "user": user,
            "assistant": target,
            "behavior": row.get("expected_behavior"),
            "intent": row.get("intent"),
            "difficulty": row.get("difficulty"),
        })
    return out


# --------------------------------------------------------------------------- #
# output formats
# --------------------------------------------------------------------------- #


def write_chatml(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"messages": [
                {"role": "system", "content": r["system"]},
                {"role": "user", "content": r["user"]},
                {"role": "assistant", "content": r["assistant"]},
            ]}, ensure_ascii=False) + "\n")


def write_mlx(rows: list[dict], path: Path) -> None:
    """mlx_lm.lora wants a flat `text` field with the chat template applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            text = (f"<|im_start|>system\n{r['system']}<|im_end|>\n"
                    f"<|im_start|>user\n{r['user']}<|im_end|>\n"
                    f"<|im_start|>assistant\n{r['assistant']}<|im_end|>")
            f.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")


def token_stats(rows: list[dict]) -> dict:
    """Approximate token counts. ~4 characters per token is close enough for
    choosing max_seq_len; the exact figure depends on the tokenizer, and the
    decision it informs ("is 2048 enough?") is not a close call either way."""
    lengths = [(len(r["system"]) + len(r["user"]) + len(r["assistant"])) // 4
               for r in rows]
    lengths.sort()

    def pct(p: float) -> int:
        if not lengths:
            return 0
        return lengths[min(len(lengths) - 1, int(p / 100 * (len(lengths) - 1)))]

    return {"n": len(lengths), "min": lengths[0] if lengths else 0,
            "p50": pct(50), "p90": pct(90), "p99": pct(99),
            "max": lengths[-1] if lengths else 0}


def main() -> int:
    ap = argparse.ArgumentParser(description="prepare DeskPlan for fine-tuning")
    ap.add_argument("--format", choices=["all", "chatml", "mlx"], default="all")
    ap.add_argument("--max-seq-len", type=int, default=2048)
    args = ap.parse_args()

    stats: dict = {}
    total = 0
    for split, out_name in SPLITS.items():
        rows = build_rows(split)
        total += len(rows)
        if args.format in ("all", "chatml"):
            write_chatml(rows, OUT / "chatml" / f"{out_name}.jsonl")
        if args.format in ("all", "mlx"):
            write_mlx(rows, OUT / "mlx" / f"{out_name}.jsonl")

        by_behavior: dict[str, int] = {}
        for r in rows:
            by_behavior[r["behavior"]] = by_behavior.get(r["behavior"], 0) + 1
        stats[out_name] = {"rows": len(rows), "by_behavior": by_behavior,
                           "tokens": token_stats(rows)}
        print(f"{out_name:6s} {len(rows):5d} rows  {by_behavior}")
        print(f"       tokens ~ p50 {stats[out_name]['tokens']['p50']}  "
              f"p99 {stats[out_name]['tokens']['p99']}  "
              f"max {stats[out_name]['tokens']['max']}")

    (OUT / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    p99 = max(s["tokens"]["p99"] for s in stats.values())
    longest = max(s["tokens"]["max"] for s in stats.values())
    print()
    print(f"{total} training examples written to {OUT.relative_to(ROOT)}")
    print()
    if longest > args.max_seq_len:
        print(f"WARNING: the longest example is ~{longest} tokens but max_seq_len "
              f"is {args.max_seq_len}.")
        print("Truncation removes the END of the target — the closing braces of the")
        print("JSON plan — so every truncated row teaches the model to emit invalid")
        print("JSON. Raise max_seq_len or drop the long rows; do not just proceed.")
    else:
        print(f"p99 ~{p99} tokens, longest ~{longest}: max_seq_len="
              f"{args.max_seq_len} is comfortable (docs/05 §3 says check this).")
    print()
    print("Next:")
    print("  local  : mlx_lm.lora --model mlx-community/Qwen2.5-3B-Instruct-4bit \\")
    print("             --train --data training/data/mlx --batch-size 4 --iters 1200")
    print("  Colab  : python training/train_lora.py --help")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
