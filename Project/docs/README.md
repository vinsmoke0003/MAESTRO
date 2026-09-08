# Where the specification lives in the code

The design documents are in [`../../docs/`](../../docs) — the eight files written
in the 7th semester. This page is the index from those documents into this
implementation, for the examiner who asks "where is §6 actually implemented?"

## Specification → module

| Spec | Section | Implemented in | Pinned by |
|---|---|---|---|
| 01 PRD | §4.2 out-of-scope actions | `maestro/nlp/classifier.py` `UNSAFE_PATTERNS`, `maestro/registry.py` `hard_blocked` | `test_nlp.py::test_unsafe_requests_are_caught_by_the_prefilter` |
| 01 PRD | FR-06 ask when unsure | `maestro/nlp/clarify.py` | `test_nlp.py::test_ambiguous_destructive_asks_instead_of_acting` |
| 01 PRD | FR-26 trash never unlink | `maestro/executor/fs.py` `TrashExecutor`; `fs.delete_permanent` has no executor | `test_executors.py::test_email_send_has_no_executor_at_all` |
| 01 PRD | NFR-07 deterministic safety | `maestro/safety/scorer.py` — no LLM call anywhere in the package | `test_safety.py::test_scoring_is_deterministic` |
| 02 Architecture | §3 Action IR | `maestro/ir/model.py` | `test_ir.py` |
| 02 Architecture | §3.3 verb registry | `maestro/registry.py` + `register()` calls in `maestro/executor/*.py` | `test_safety.py::test_every_registered_verb_has_an_executor_or_is_blocked` |
| 02 Architecture | §4 request lifecycle | `maestro/pipeline.py` `MaestroPipeline.handle()` | `test_pipeline.py` |
| 02 Architecture | §5 multi-agent structure | `maestro/agents/` (Summarizer, Verifier), `maestro/planner/critic.py` | `test_agents.py` |
| 02 Architecture | §6 trust model | `maestro/safety/taint.py`; Summarizer has no tools by construction | `test_agents.py::test_summarizer_has_no_tool_access`, `test_planner.py::test_the_planner_has_no_channel_for_untrusted_content` |
| 02 Architecture | §6 rule 4 fixed DAG | `maestro/orchestrator.py` iterates `plan.topo_order` captured before execution | `test_orchestrator.py::test_the_plan_cannot_grow_during_execution` |
| 02 Architecture | §7 cross-platform boundary | `maestro/executor/platform.py` is the only file allowed to branch on the OS | `scripts/check_platform_boundary.py`, run in CI |
| 02 Architecture | §8 data model | `maestro/memory/episodes.py`, `maestro/safety/audit.py` | `test_memory.py`, `test_safety.py::test_audit_tampering_is_detected` |
| 05 NLP & Training | §1 three-stage pipeline | `maestro/nlp/classifier.py`, `entities.py`, `maestro/planner/planner.py` | `test_nlp.py`, `test_planner.py` |
| 05 NLP & Training | §2 DeskPlan dataset | `data/build_dataset.py`, `data/validate_dataset.py` | `test_dataset_and_eval.py::test_dataset_validator_passes` |
| 05 NLP & Training | §2 split by paraphrase group | `data/build_dataset.py` `split_by_group()` | `test_dataset_and_eval.py::test_no_paraphrase_group_straddles_a_split` |
| 05 NLP & Training | §2 adversarial is test-only | `data/build_dataset.py` (`split: "test"` hard-coded) | `test_dataset_and_eval.py::test_adversarial_rows_are_test_only` |
| 05 NLP & Training | §3 LoRA fine-tune | `training/prepare_data.py`, `train_lora.py`, `configs/lora.yaml` | **future work** — pipeline written and unit-tested, never trained (needs a GPU; no result is claimed) |
| 05 NLP & Training | §3 identical train/inference prompt | `maestro/planner/prompts.py` `training_prompt()` | `test_planner.py::test_training_and_inference_share_the_same_system_prompt` |
| 05 NLP & Training | §4 classical baselines + latency | `training/train_intent.py --compare` | — |
| 06 Safety Spec | §2 risk taxonomy & `score_risk` | `maestro/safety/scorer.py` — rule order mirrors the pseudocode | `test_safety.py` |
| 06 Safety Spec | §2 path policy, canonicalise first | `maestro/safety/paths.py` | `test_safety.py::test_traversal_does_not_escape_the_allowlist` |
| 06 Safety Spec | §2 hint recorded, never trusted | `ActionVerdict.hint` / `hint_agrees` | `test_safety.py::test_risk_hint_is_recorded_but_ignored` |
| 06 Safety Spec | §3 dry-run simulator | every executor's `dry_run()`; `Orchestrator._dry_run()`; `render_preview()` | `test_orchestrator.py::test_nothing_touches_the_disk_before_consent` |
| 06 Safety Spec | §4 consent model, anti-habituation | `maestro/safety/consent.py` | `test_safety.py::test_low_risk_never_prompts`, `test_a_click_cannot_satisfy_a_typed_confirmation` |
| 06 Safety Spec | §5 hard blocks, no override | `VerbSpec.hard_blocked`; the verb exists but no executor is registered | `test_safety.py::test_hard_blocked_verb_has_no_override` |
| 06 Safety Spec | §6 injection controls, per-control attribution | `ActionVerdict.rules_fired`, `PlanVerdict.first_control`, `eval/harness.py::_control_fired` | `eval/report.py` Table 6b |
| 06 Safety Spec | §7 what we do not claim | `eval/report.py::caveats()` — generated from the run | — |
| 07 Evaluation | §2 metrics | `eval/metrics.py` | `test_dataset_and_eval.py::test_uer_is_measured_against_ground_truth_not_self_report` |
| 07 Evaluation | §3 100-task suite + adversarial | `eval/build_benchmark.py` → `eval/tasks/` | `test_dataset_and_eval.py::test_benchmark_has_100_tasks_in_the_documented_distribution` |
| 07 Evaluation | §3 hermetic fixtures | `eval/fixtures.py` `sandbox()` | — |
| 07 Evaluation | §4 baselines B0–B4 | `eval/harness.py` `CONFIGS` | — |
| 07 Evaluation | §5 ablations A0–A8 | `eval/harness.py` `CONFIGS`; switches on `MaestroPipeline` / `Orchestrator` | `test_orchestrator.py::test_postconditions_can_be_ablated` |
| 07 Evaluation | §6 statistics | `eval/metrics.py` `bootstrap_ci`, `mcnemar`, `cohens_d` | — |
| 07 Evaluation | §7 the ten tables | `eval/report.py` | — |
| 03 Tech stack | §3 provider-agnostic LLM + response cache | `maestro/llm/base.py`, `router.py` | `test_planner.py` (all LLM tests use `ScriptedClient`) |

## Deliberate departures from the specification

Each is a judgement call; each should be stated in the report rather than
discovered by the examiner.

| Spec says | This does | Why |
|---|---|---|
| 06 §2: any path outside the allowlist → R2 | `sys.info`'s `path` is not a path argument at all | It selects a *volume* for `disk_usage`, not a file. Escalating "how much disk space is left?" to a consent prompt is the false-confirmation habituation failure 06 §4 warns about. |
| 07 §3: 40 adversarial cases | 50 | The 40 attacks, plus 10 `should_not_refuse` controls. Without them a system that refuses everything scores 100% IRR. |
| 05 §2: 1,400 LLM-generated, human-verified pairs | 0 shipped; tooling provided | Generating them needs a model and a human reviewer. `data/generate_llm_pairs.py` and `data/verify_candidates.py` do both halves, but this build does not fabricate rows and label them `llm_generated`. |
| 02 §3.3: `mdfind` / Windows Search for `search.*` | plain directory walk | Indexed search is non-deterministic across machines (index state differs), which would break the cross-platform equivalence test. Same verb, swappable backend. |
| 07 §5 A8: trust isolation ablated | safety layer + prefilter removed | MAESTRO cannot run without trust isolation — the Summarizer has no tools *structurally*. A8 is as close as the architecture permits and is labelled as such. |
| 07 §3: task categories T3/T4 run everywhere | browser tasks need Playwright; desktop tasks are opt-in | `app.launch` opens real applications and cannot be sandboxed; skipping is reported, never counted as failure. |

## Reproducing the numbers

```bash
make all
```

Dataset → benchmark → intent model → lint + boundary + tests → evaluation →
report, with pinned seeds. NFR-08 as a command.
