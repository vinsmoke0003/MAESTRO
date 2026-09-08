"""LoRA fine-tune the planner (docs/05-NLP-AND-TRAINING.md §3).

    python training/train_lora.py --check          # environment report, no training
    python training/train_lora.py                  # train with configs/lora.yaml
    python training/train_lora.py --base Qwen/Qwen2.5-1.5B-Instruct --rank 8

This needs a GPU (or Apple Silicon) and `peft` + `transformers` + `trl`, none of
which are project dependencies — the rest of MAESTRO runs without them, and a
laptop with no GPU should not be made to install torch to run the tests. Use
`--check` first: it reports exactly what is missing and what to run.

**Where to actually run this** (docs/05 §3):

* **Apple Silicon** — use MLX instead of this script. It is faster, has no
  session limit and no disconnect risk:

      mlx_lm.lora --model mlx-community/Qwen2.5-3B-Instruct-4bit --train \\
        --data training/data/mlx --batch-size 4 --iters 1200 --lora-layers 16

* **Kaggle (~30 GPU-hr/week) or Colab free T4** — this script, with Unsloth if
  available. Checkpoint every epoch to Drive; free sessions get reclaimed.

**Report mean +/- std over three seeds.** docs/05 §3 is explicit that a
single-seed result is not evidence. `--seed` exists so you can run it three
times; `evaluate_planner.py` aggregates.

**The honest-result rule.** The research question is whether a 3B model
fine-tuned on ~3k pairs matches a frontier model at zero cost. If the answer is
no, that is also publishable *provided you characterise where it fails* — long
plans, rare verbs, ambiguity. `evaluate_planner.py --by-difficulty --by-verb`
produces exactly that breakdown. Decide now to report whichever answer comes
out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "training" / "data" / "chatml"
CONFIG = ROOT / "training" / "configs" / "lora.yaml"
OUTPUT = ROOT / "models" / "planner-lora"

DEFAULTS = {
    "base_model": "Qwen/Qwen2.5-3B-Instruct",
    "rank": 16,
    "alpha": 32,
    "dropout": 0.05,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                       "gate_proj", "up_proj", "down_proj"],
    "learning_rate": 2e-4,
    "epochs": 3,
    "batch_size": 4,
    "grad_accum": 4,
    "max_seq_len": 2048,
    "warmup_ratio": 0.03,
    "lr_scheduler": "cosine",
    "precision": "bf16",
    "seed": 42,
}


def load_config(path: Path) -> dict:
    cfg = dict(DEFAULTS)
    if not path.exists():
        return cfg
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        cfg.update(yaml.safe_load(text) or {})
        return cfg
    except ImportError:
        # PyYAML is not a project dependency either. The config is a flat
        # key: value file, so a five-line parser avoids the dependency.
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, raw = line.partition(":")
            raw = raw.strip()
            if not raw:
                continue
            if raw.startswith("["):
                value: object = [x.strip().strip("'\"") for x in
                                 raw.strip("[]").split(",") if x.strip()]
            elif raw.lower() in ("true", "false"):
                value = raw.lower() == "true"
            else:
                try:
                    value = int(raw)
                except ValueError:
                    try:
                        value = float(raw)
                    except ValueError:
                        value = raw.strip("'\"")
            cfg[key.strip()] = value
        return cfg


def check_environment() -> tuple[bool, list[str]]:
    notes: list[str] = []
    ok = True

    for name, hint in [
        ("torch", "pip install torch  (or the CUDA build for your GPU)"),
        ("transformers", "pip install transformers"),
        ("peft", "pip install peft"),
        ("datasets", "pip install datasets"),
        ("trl", "pip install trl"),
    ]:
        try:
            __import__(name)
            notes.append(f"  [ok]      {name}")
        except ImportError:
            ok = False
            notes.append(f"  [missing] {name:14s} {hint}")

    try:
        import torch

        if torch.cuda.is_available():
            notes.append(f"  [ok]      CUDA: {torch.cuda.get_device_name(0)}")
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            notes.append("  [ok]      Apple MPS available — prefer mlx_lm.lora instead")
        else:
            ok = False
            notes.append("  [missing] no GPU. A 3B LoRA on CPU is not practical; "
                         "use Kaggle or Colab.")
    except ImportError:
        pass

    for split in ("train", "valid"):
        p = DATA / f"{split}.jsonl"
        if p.exists():
            n = sum(1 for _ in p.open(encoding="utf-8"))
            notes.append(f"  [ok]      {p.relative_to(ROOT)} ({n} rows)")
        else:
            ok = False
            notes.append(f"  [missing] {p.relative_to(ROOT)} — run "
                         f"`python training/prepare_data.py`")
    return ok, notes


def train(cfg: dict) -> int:
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
    from trl import SFTConfig, SFTTrainer

    set_seed(int(cfg["seed"]))
    print(f"base model     {cfg['base_model']}")
    print(f"LoRA           r={cfg['rank']} alpha={cfg['alpha']} "
          f"dropout={cfg['dropout']}")
    print(f"schedule       lr={cfg['learning_rate']} epochs={cfg['epochs']} "
          f"batch={cfg['batch_size']}x{cfg['grad_accum']} seed={cfg['seed']}")

    tokenizer = AutoTokenizer.from_pretrained(cfg["base_model"])
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    ds = load_dataset("json", data_files={
        "train": str(DATA / "train.jsonl"),
        "validation": str(DATA / "valid.jsonl"),
    })

    model = AutoModelForCausalLM.from_pretrained(
        cfg["base_model"],
        torch_dtype="bfloat16" if cfg["precision"] == "bf16" else "auto",
        device_map="auto",
    )

    peft_config = LoraConfig(
        r=int(cfg["rank"]),
        lora_alpha=int(cfg["alpha"]),
        lora_dropout=float(cfg["dropout"]),
        target_modules=list(cfg["target_modules"]),
        bias="none",
        task_type="CAUSAL_LM",
    )

    out_dir = OUTPUT / f"r{cfg['rank']}-seed{cfg['seed']}"
    sft = SFTConfig(
        output_dir=str(out_dir),
        num_train_epochs=int(cfg["epochs"]),
        per_device_train_batch_size=int(cfg["batch_size"]),
        gradient_accumulation_steps=int(cfg["grad_accum"]),
        learning_rate=float(cfg["learning_rate"]),
        lr_scheduler_type=str(cfg["lr_scheduler"]),
        warmup_ratio=float(cfg["warmup_ratio"]),
        max_length=int(cfg["max_seq_len"]),
        bf16=cfg["precision"] == "bf16",
        logging_steps=20,
        eval_strategy="epoch",
        # Checkpoint every epoch: docs/05 §3 — free Colab/Kaggle sessions are
        # reclaimed without warning, and an un-checkpointed 3-hour run is lost.
        save_strategy="epoch",
        save_total_limit=3,
        report_to=[],
        seed=int(cfg["seed"]),
    )

    trainer = SFTTrainer(
        model=model,
        args=sft,
        train_dataset=ds["train"],
        eval_dataset=ds["validation"],
        peft_config=peft_config,
        processing_class=tokenizer,
    )
    result = trainer.train()
    trainer.save_model(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))

    (out_dir / "training_summary.json").write_text(json.dumps({
        "config": dict(cfg),
        "metrics": dict(result.metrics),
    }, indent=2), encoding="utf-8")

    print()
    print(f"adapter saved to {out_dir}")
    print()
    print("Next:")
    print("  1. Serve it:   create a Modelfile with `FROM <base>` + `ADAPTER "
          "<path>`, then `ollama create maestro-planner -f Modelfile`")
    print("  2. Point MAESTRO at it:  MAESTRO_MODEL=maestro-planner")
    print("  3. Evaluate:   python training/evaluate_planner.py --model "
          "maestro-planner")
    print("  4. Repeat with --seed 43 and --seed 44, then report mean +/- std.")
    print("     A single-seed number is not a result (docs/05 §3).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LoRA fine-tune the MAESTRO planner")
    ap.add_argument("--check", action="store_true", help="environment report only")
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--base", default=None, help="override base_model")
    ap.add_argument("--rank", type=int, default=None)
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    for key, value in [("base_model", args.base), ("rank", args.rank),
                       ("epochs", args.epochs), ("seed", args.seed)]:
        if value is not None:
            cfg[key] = value

    ok, notes = check_environment()
    print("environment")
    print("\n".join(notes))
    print()

    if args.check:
        print("ready to train" if ok else
              "not ready — install what is marked missing above")
        print()
        print("On Apple Silicon prefer MLX (no session limit, no disconnects):")
        print("  mlx_lm.lora --model mlx-community/Qwen2.5-3B-Instruct-4bit "
              "--train \\")
        print("    --data training/data/mlx --batch-size 4 --iters 1200 "
              "--lora-layers 16")
        return 0 if ok else 1

    if not ok:
        print("cannot train: install the missing packages, or run with --check "
              "for guidance.")
        return 1
    return train(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
