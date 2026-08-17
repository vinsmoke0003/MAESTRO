# 05 — NLP Layer, Dataset & Fine-Tuning

**Project MAESTRO** · v1.0

This is your **ML contribution** — the part that makes it a CSE research project rather than an integration exercise. When you chose "fine-tune a small model on intent→plan pairs," you chose the option that produces a real artifact (a dataset), a real experiment (does fine-tuning a 3B model close the gap to a frontier model?), and a real result. Treat this document as the plan for the thing your paper will actually be about.

---

## 1. The NLP Pipeline

Three stages, deliberately not one big LLM call:

```
"move last month's invoices to the finance folder"
        │
   ┌────▼─────────────────────────────────────────┐
   │ STAGE 1 — Intent Classification              │
   │ Fine-tuned 1.5B  (or DistilBERT baseline)    │
   │ → FILE_ORGANIZE, confidence 0.94             │
   └────┬─────────────────────────────────────────┘
        │
   ┌────▼─────────────────────────────────────────┐
   │ STAGE 2 — Entity / Slot Extraction           │
   │ spaCy NER + rules + LLM fallback             │
   │ → filetype=invoice · timeframe=last_month    │
   │   dest=~/Documents/Finance (from memory)     │
   │   source=UNFILLED  ← triggers clarification  │
   └────┬─────────────────────────────────────────┘
        │
   ┌────▼─────────────────────────────────────────┐
   │ STAGE 3 — Plan Generation                    │
   │ Fine-tuned 3B (or 7B zero-shot)              │
   │ constrained JSON decoding → Action IR DAG    │
   └──────────────────────────────────────────────┘
```

**Why three stages instead of one prompt?** Four reasons, all of which you should state in the report:

1. **Each stage is separately measurable.** A single end-to-end call gives you one number and no diagnosis. Staged, you can say *"planning is fine; 60% of our failures are entity resolution"* — which is a finding.
2. **Small models handle stages 1–2 well.** Intent classification over ~15 classes does not need 7B parameters. Routing the easy work to a 1.5B model is most of your latency budget.
3. **Confidence is available for gating.** A classifier gives you a calibrated score; a generative model's "I think it's FILE_ORGANIZE" does not. FR-06 (ask when unsure) depends on this.
4. **You get an ablation for free.** Staged-vs-monolithic is a legitimate experimental comparison and costs you nothing extra to run.

### Intent taxonomy (v1 — ~15 classes)

`FILE_ORGANIZE` · `FILE_SEARCH` · `FILE_DELETE` · `FILE_TRANSFORM` · `APP_LAUNCH` · `APP_CONTROL` · `BROWSER_NAVIGATE` · `BROWSER_EXTRACT` · `BROWSER_DOWNLOAD` · `SYSTEM_QUERY` · `SYSTEM_SETTING` · `COMPOSE_DRAFT` · `WORKFLOW_RECALL` · `CLARIFY_RESPONSE` · `OUT_OF_SCOPE` · `UNSAFE_REQUEST`

The last two matter more than they look. `OUT_OF_SCOPE` and `UNSAFE_REQUEST` are the classes that make correct refusal a *trained behavior with a measurable accuracy*, rather than an accident of prompt wording. Report per-class F1 and put those two rows in bold.

### Entity types
`PATH` · `FILE_TYPE` · `FILE_NAME` · `APP_NAME` · `URL` · `DATETIME` · `DURATION` · `QUANTITY` · `PERSON` · `EMAIL` · `SETTING_KEY` · `SETTING_VALUE` · `WORKSPACE_REF`

Resolution order for each slot: **explicit in instruction → memory/preferences → OS defaults → ask the user.** Never guess a path. A wrong path guess on an `fs.move_batch` is exactly the failure mode this whole project exists to prevent, and it would be embarrassing to have it originate in your own entity resolver.

---

## 2. The Dataset

**Name:** `DeskPlan` — Natural Language to Safe Desktop Action Plans
**Target:** 3,000+ verified pairs + 200 adversarial
**Licence:** decide by 8th-sem M6 (MIT or CC-BY-4.0)
**Release:** Hugging Face Datasets + GitHub

A public dataset is the most durable thing you will produce. Papers get skimmed; datasets get downloaded and cited. Budget real time for it.

### Record schema

```jsonc
{
  "id": "dp_00417",
  "instruction": "move last month's invoices to the finance folder",
  "paraphrase_group": "pg_0091",        // paraphrases share a group; must not split across train/test
  "context": {
    "platform": "darwin",
    "cwd": "~/Downloads",
    "known_paths": { "finance": "~/Documents/Finance" },
    "date": "2026-09-15"
  },
  "intent": "FILE_ORGANIZE",
  "entities": [
    { "type": "FILE_TYPE", "value": "invoice", "span": [17, 25] },
    { "type": "DATETIME",  "value": "last_month", "span": [5, 15] }
  ],
  "plan": { /* full Action IR — the generation target */ },
  "plan_risk": "R2",
  "expected_behavior": "execute_with_consent",
   // execute_auto | execute_with_consent | clarify | refuse
  "difficulty": "medium",              // easy | medium | hard
  "source": "human",                   // human | template | llm_generated
  "verified_by": "annotator_2",
  "notes": "'last month' resolves relative to context.date"
}
```

`paraphrase_group` is easy to skip and expensive to skip. If "move my PDFs to Documents" lands in train and "shift the PDFs into Documents" lands in test, your test accuracy is inflated and a careful examiner will notice. Split by group, always.

### Composition targets

| Source | Count | Method |
|---|---|---|
| **Human-written seed** | 400 | Team writes them. Includes sloppy, ambiguous, and multi-step phrasings. |
| **Template expansion** | 1,200 | Slot-filling grammars over paths, filetypes, apps, timeframes. Cheap, high precision, low diversity. |
| **LLM-generated, human-verified** | 1,400 | Free Gemini/Groq tier generates paraphrases + novel instructions; **every one is human-checked before entry.** |
| **Adversarial** | 200 | Unsafe instructions + injection payloads. Hand-written. |
| **Total** | **3,200** | |

**The verification rule is absolute: no LLM-generated pair enters the dataset unverified.** An unverified synthetic dataset teaches your model the generator's mistakes, and the resulting paper is not credible. Log verification rate — "we discarded 23% of generated candidates" is a *good* number to report, not an embarrassing one.

### Difficulty tiers
- **Easy** — single action, all entities explicit. *"Open Chrome."*
- **Medium** — 2–4 actions, one implicit entity. *"Move the PDFs from Downloads to Documents."*
- **Hard** — 5+ actions, conditionals, memory reference, or ambiguity requiring clarification. *"Organize my semester files the way I did last time, but skip anything already in the archive."*

Aim 30/50/20. Too many easy pairs and the metrics look great and mean nothing.

### Splits
80 / 10 / 10 by `paraphrase_group`, stratified on intent and difficulty. Adversarial cases are **test-only** — never train on them, or your injection-resistance number is meaningless.

### Annotation quality
Write the guidelines in Week 10 before annotating. Double-annotate a 50-pair overlap, compute **Cohen's κ** on intent and on plan equivalence, and report it. Target κ > 0.75. If you come in lower, the guidelines are ambiguous — fix them and re-annotate rather than shipping a noisy dataset.

---

## 3. Fine-Tuning

### Why fine-tune at all (the research question)

> **RQ:** Can a 3B open model, LoRA-fine-tuned on 3,000 domain pairs, match a frontier model's planning accuracy on desktop automation — at zero marginal cost, with full local privacy, and low enough latency for interactive use?

That is a clean, falsifiable question with a genuinely uncertain answer, which is what makes it worth running. If yes, you have shown small local models are sufficient for a safety-critical agent domain — a useful result. **If no, that is also a publishable finding**, provided you characterize *where* it fails (long plans? rare verbs? ambiguity?). Decide now that you will report the honest answer either way; it protects you from the temptation to tune the evaluation until the story is nice.

### Configuration

| Parameter | Value | Note |
|---|---|---|
| Base model | Qwen2.5-3B-Instruct | Also try Llama-3.2-3B and Qwen2.5-1.5B as ablations |
| Method | LoRA | Full fine-tuning won't fit and isn't needed |
| Rank `r` | 16 | Sweep {8, 16, 32} |
| Alpha | 32 | 2× rank |
| Dropout | 0.05 | |
| Target modules | `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj` | |
| LR | 2e-4 | Cosine schedule, 3% warmup |
| Epochs | 3 | Watch val loss; 3B on 3k pairs overfits by ~epoch 4 |
| Batch | 4 × grad-accum 4 | Effective 16 |
| Max seq len | 2048 | Plans are long; check your p99 token length before fixing this |
| Precision | bf16 | |
| Seed | 42, plus 2 reruns | Report mean ± std — single-seed results are not evidence |

### Where to run it

**Primary — local MLX on your M2 Pro.** LoRA on a 3B model fits comfortably in 16 GB and there is no session limit, no queue, and no disconnect risk. Expect a few hours for 3 epochs.

```bash
mlx_lm.lora --model mlx-community/Qwen2.5-3B-Instruct-4bit --train --data ./data/mlx --batch-size 4 --iters 1200 --lora-layers 16
```

**Secondary — Kaggle (≈30 GPU-hr/week) or Colab free T4** via Unsloth, for parallel hyperparameter sweeps or a larger base model. Checkpoint every epoch to Drive; free sessions get reclaimed without warning.

### Prompt format

Keep it identical between training and inference. A format mismatch here is the most common cause of "the fine-tune made it worse," and it is invisible unless you look for it.

```
<|im_start|>system
You are MAESTRO's planner. Convert the instruction into a JSON Action IR plan.
You may only use verbs from this registry: {verb_registry}
Respond with JSON only.<|im_end|>
<|im_start|>user
Platform: {platform}
Known paths: {known_paths}
Date: {date}
Instruction: {instruction}<|im_end|>
<|im_start|>assistant
{plan_json}<|im_end|>
```

### Evaluation of the fine-tune

| Metric | Definition |
|---|---|
| **Plan Exact Match** | Generated plan ≡ gold plan after canonicalization (sorted args, normalized paths) |
| **Action-level F1** | Treat actions as a set of `(verb, canonical_args)`; precision/recall/F1 |
| **Verb accuracy** | Correct verb sequence, ignoring arg values — isolates "knows what to do" from "knows the details" |
| **Schema validity rate** | % parsing and validating against the IR schema |
| **DAG validity rate** | % with acyclic, fully-resolvable dependencies |
| **Risk-hint agreement** | Agreement between planner's risk guess and the deterministic scorer — measures whether the model *understands* risk (it is never trusted for it) |
| **Refusal accuracy** | Correct `refuse` / `clarify` on `UNSAFE_REQUEST` and `OUT_OF_SCOPE` |
| **Latency p50/p95** | On-device, measured |

### Models to compare (this is your results table)

| ID | Model | Cost | Private | Expectation |
|---|---|---|---|---|
| M0 | Rule-based / template matcher | ₹0 | ✅ | Floor. Proves the task isn't trivial. |
| M1 | Qwen2.5-3B zero-shot | ₹0 | ✅ | Weak — poor schema adherence expected |
| M2 | Qwen2.5-7B zero-shot | ₹0 | ✅ | Solid baseline |
| M3 | **Qwen2.5-3B + LoRA (ours)** | ₹0 | ✅ | **The contribution.** Should beat M2 while being ~2× faster |
| M4 | Qwen2.5-7B + LoRA | ₹0 | ✅ | Upper bound of the local approach |
| M5 | Frontier model, free tier | ₹0* | ❌ | Ceiling. *Free tier, but not private and not offline. |

The interesting cell is **M3 vs. M5**. If a fine-tuned 3B running locally on a student laptop lands within a few points of a frontier model on this task, that is a real result with a clear practical implication — and the privacy/offline column is what makes it matter rather than just being a smaller number.

---

## 4. Classical NLP Baselines (do these — they are cheap marks)

Your project is specialized in NLP, and examiners in an NLP-specialized track will expect to see conventional NLP methodology, not only prompting. These take about two days total and give you a proper comparison chapter.

| Task | Baselines |
|---|---|
| Intent classification | TF-IDF + LinearSVC · TF-IDF + Logistic Regression · fastText · DistilBERT fine-tuned · **your fine-tuned LLM** |
| Entity extraction | Regex + gazetteer · spaCy `en_core_web_sm` · spaCy custom-trained NER · **LLM extraction** |

Report accuracy, macro-F1, per-class F1, confusion matrix, **and inference latency**. The latency column often makes the argument for you: if DistilBERT hits 96% at 8 ms and the 3B LLM hits 97% at 400 ms, the correct engineering decision is DistilBERT for stage 1 — and *making that call on evidence* is exactly what a good report demonstrates. Don't assume the biggest model wins; measure it and then justify what you shipped.
