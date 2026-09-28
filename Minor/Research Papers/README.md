# Research Papers — Reading Guide

Six open-access papers from arXiv, downloaded 27 Sep 2026. They add to the four
base papers in [`docs/base-papers/`](../../docs/base-papers) (OSWorld, Agent S,
Greshake et al., AgentDojo) and do not duplicate them.

| # | Paper | arXiv | Use it for |
|---|---|---|---|
| 01 | **CaMeL — Defeating Prompt Injections by Design** (Debenedetti et al., 2025) | [2503.18813](https://arxiv.org/abs/2503.18813) | Closest prior work to MAESTRO's thesis. It separates the control flow (trusted) from the data (untrusted) and enforces capabilities in code, not in the model. Compare it to the taint tracker and the tool-less Summarizer. **Minor: literature review. Major: related work, and the difference from MAESTRO.** |
| 02 | **ToolEmu — Identifying the Risks of LM Agents with an LM-Emulated Sandbox** (Ruan et al., 2023) | [2309.15817](https://arxiv.org/abs/2309.15817) | Shows how often tool-using agents take risky actions, and defines a failure taxonomy. Motivates the deterministic R0–R3 scorer and the dry-run preview. **Minor: problem statement.** |
| 03 | **InjecAgent — Benchmarking Indirect Prompt Injections in Tool-Integrated LLM Agents** (Zhan et al., 2024) | [2403.02691](https://arxiv.org/abs/2403.02691) | Attack categories (direct harm vs data stealing) and the attack-success-rate metric. Backs the 50-case adversarial suite and the injection-resistance metric. **Major: evaluation chapter.** |
| 04 | **UFO — A UI-Focused Agent for Windows OS Interaction** (Zhang et al., Microsoft, 2024) | [2402.07939](https://arxiv.org/abs/2402.07939) | The nearest desktop agent that uses GUI control. It is a contrast to MAESTRO's closed verb registry: UFO has broad capability and less of a safety envelope. **Minor/Major: related work and comparison table.** |
| 05 | **ReAct — Synergizing Reasoning and Acting in Language Models** (Yao et al., 2022) | [2210.03629](https://arxiv.org/abs/2210.03629) | The foundational agent loop (think → act → observe). MAESTRO departs from it on purpose: the plan DAG is frozen at consent time, with no re-planning mid-run. Expect a viva question on this. **Minor: background.** |
| 06 | **LoRA — Low-Rank Adaptation of Large Language Models** (Hu et al., 2021) | [2106.09685](https://arxiv.org/abs/2106.09685) | The method behind `Project/training/train_lora.py` (Qwen2.5-3B, rank 16). **Major: training methodology / future work.** |

## Suggested reading order

1. **ReAct**: how LLM agents work.
2. **ToolEmu**: why they are risky.
3. **InjecAgent**: the injection threat, measured.
4. **CaMeL**: a design-level defence, the one to compare MAESTRO against.
5. **UFO**: what an unconstrained desktop agent looks like.
6. **LoRA**: only when you start the fine-tuning work.
