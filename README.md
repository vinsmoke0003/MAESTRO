# MAESTRO

**Multi-Agent Execution System for Task Reasoning and Orchestration**

**Design and Evaluation of a Safe Multi-Agent System for Natural Language-Driven Desktop Task Automation**

[![CI](https://github.com/vinsmoke0003/MAESTRO/actions/workflows/ci.yml/badge.svg)](https://github.com/vinsmoke0003/MAESTRO/actions/workflows/ci.yml)

MAESTRO is a local-first desktop assistant that takes an instruction in plain English and carries it out on a real computer, and refuses to do it blindly. Every planned action is compiled into a typed, inspectable intermediate representation, scored for risk by deterministic code, dry-run before anything touches the disk, gated behind human confirmation when it crosses a threshold, and written to a tamper-evident audit log.

The project does not claim to make LLM automation "fully safe". Its research claim is narrower, and it is the one that can be defended and measured:

> **MAESTRO makes desktop automation auditable, reversible, consent-gated, and injection-resistant.**

The rule the whole design follows: **the model proposes; deterministic code decides.**

---

## Project Context

| Field | Detail |
|---|---|
| Institution | Amity School of Engineering & Technology |
| Programme | B.Tech CSE (Evening), Session 2023-27 |
| Group | 298 |
| Guide | Dr. Rajni Sehgal Kaushik |
| Area | Agentic AI with specialization in Natural Language Processing |
| Team | Shashank Gupta, Seenu, Jairaj Berry |
| Minor Project (7th semester) | Research, design, safety model, evaluation methodology |
| Major Project (8th semester) | Implementation v1.0, DeskPlan dataset, 100-task benchmark, evaluation matrix |
| Cost constraint | INR 0 marginal cost, local-first stack, no paid API |

---

## The Result In One Table

Run `full_matrix`, 2026-09-08, Windows 11, Python 3.13, **three seeds**. 88 of 100 benchmark tasks attempted per seed (12 desktop tasks are opt-in because they open real applications), all 50 adversarial cases.

| | B1: the naive agent | B3: MAESTRO |
|---|---|---|
| Attempted / completed / failed / skipped (x3 seeds) | 264 / 258 / 6 / 12 | 264 / 261 / 3 / 12 |
| Task Success Rate | 97.7% | 98.9% |
| **Unsafe Execution Rate** | **100%** | **0%** |
| False Confirmation Rate | 0% | **0%** |
| Injection Resistance Rate | 60% | **100%** |
| Hard Block Rate | 0% | **100%** |
| Undo Round-trip (content-verified) | 100% | 100% |
| Audit Integrity | 100% | 100% |

B1 is the same executors, the same tasks and the same planner with the safety layer removed. On this suite the safety layer costs no task success, takes unsafe execution from every opportunity to none, and never prompts on a low-risk action.

A safety layer that costs nothing should raise suspicion, and the project says so. The benchmark and the deterministic planner were written from the same task taxonomy, so the honest ceiling is in the held-out set: **67.4%** behavioural accuracy on fresh phrasings. Read [Honest Limits](#honest-limits) before quoting any number. The full ten-table report is [`Project/eval/results/full_matrix.md`](Project/eval/results/full_matrix.md).

---

## Repository Map

```text
.
├── Project/        v1.0: the implementation, dataset, benchmark, evaluation, training  <-- run and cite this
│   ├── maestro/    the package (10,140 lines): ir, safety, planner, nlp, executor, agents, memory, llm, ui
│   ├── data/       DeskPlan dataset: generator, validator, seeds, grammar, adversarial cases, splits
│   ├── eval/       100-task benchmark, 50 adversarial cases, harness, metrics, report, held-out set
│   ├── training/   intent classifier training, LoRA pipeline, planner evaluation
│   ├── models/     the trained intent classifier (tracked, so a fresh clone works)
│   ├── tests/      321 tests across 15 modules
│   ├── scripts/    platform boundary check, cross-platform diff, UI server, Windows setup
│   ├── docs/       where each specification section lives in the code, and deliberate departures
│   ├── Makefile    every workflow as a target; `make all` rebuilds the repo from source
│   └── README.md   the developer-facing guide with the full results discussion
├── Major/          the earlier prototype (v0.3, 2,112 lines, 48 tests) plus its project report
├── Minor/          minor-project deliverables: progress reports, system design, diaries, viva Q&A, WPR 1-7
├── docs/           the specification: PRD, architecture, tech stack, roadmap, NLP, safety spec, evaluation, team
│   └── base-papers/  OSWorld, Agent S, Greshake et al. (indirect injection), AgentDojo
└── .github/        CI: three operating systems, full rebuild, safety regression gate, IR equivalence check
```

`Major/` is kept for the record. It is the minor-project build that proved the safety layer before any LLM was allowed near it. Everything current is in `Project/`.

---

## Quick Start

Everything below runs from the `Project/` directory. The core path needs only `pydantic` and `send2trash`; no model, no API key, no network.

```bash
make install
```

```bash
make test
```

```bash
make demo
```

The demo creates a fixture, plans a real task, shows the preview, asks for consent, executes, then walks through a refusal, a clarification and a prompt-injection attempt, and verifies the audit chain at the end.

```bash
python -m maestro.cli ask "move the pdfs from Downloads to Documents/Invoices"
```

```bash
python -m maestro.cli doctor
```

`doctor` probes what actually works on the machine: it launches Chromium, loads the intent model and resolves the app registry rather than reporting what is importable. Anything it marks MISSING should not be part of a live demo.

```bash
python -m maestro.cli ui
```

The web workspace runs on `127.0.0.1:8765` with the standard library only: instruction, action preview with risk tier and the rules that produced it, approve or deny, live step progress, and a task history whose completed runs carry a verified **Undo**. Approval is two-phase and fingerprinted, so the user approves the plan they saw, not the instruction.

On Windows, `scripts\setup.ps1` does all of the above in one go (venv, install, dataset, benchmark, intent model, tests, doctor; add `-WithBrowser` for Chromium), and `scripts\run.ps1 <command>` runs any subcommand without activating the venv.

### With a local model

MAESTRO plans without an LLM through its template planner. To add one:

```bash
ollama serve && ollama pull qwen2.5:7b-instruct-q4_K_M
```

It is picked up automatically. Configuration is by environment variable, documented in [`Project/.env.example`](Project/.env.example):

| Variable | Default | Purpose |
|---|---|---|
| `MAESTRO_WORKSPACE` | `~/maestro_workspace` | the allowlisted working directory |
| `MAESTRO_MODEL` | `qwen2.5:7b-instruct-q4_K_M` | Ollama model for the LLM planner |
| `MAESTRO_LLM` | `auto` | `auto`, `ollama`, `openai` or `none` |
| `MAESTRO_BULK_N` | `25` | file count above which an action is escalated to R2 |
| `MAESTRO_EVAL_DESKTOP` | `0` | opt in to benchmark tasks that open real applications |

### Optional extras

| Extra | Adds | Without it |
|---|---|---|
| `nlp` | scikit-learn intent classifier | rule-based classifier |
| `browser` | Playwright + Chromium for `browser.*` verbs | browser tasks report NotAvailable |
| `system` | psutil for memory, battery, CPU metrics | stdlib fallbacks |
| `memory` | ChromaDB + sentence-transformers exemplar store | zero-dependency hashing store |
| `train` | torch, transformers, peft, trl for LoRA | needs a GPU |

---

## How It Works

```text
 "move the pdfs from Downloads to Documents/Invoices"
    │
 L5 NLP           intent (16 classes, calibrated confidence) + entity slots
    │             ├─ unsafe? refuse here, before planning
    │             └─ slot missing or ambiguous? ask, never guess
 L4 PLANNER       LLM (constrained decoding, verb enum = the registry)
    │             or deterministic templates. Repair loop, bounded at 3.
 L3 SAFETY        closed verb registry · path allow/denylist · taint tracking
    │             deterministic R0-R3 scorer · budget guard · Critic
    │             DRY RUN: nothing has touched the disk yet
    │             ┌──────────────────────────────────────────┐
    │             │ a1. Find 47 PDFs in ~/Downloads   [safe] │
    │             │ a2. Move 47 files -> Invoices   [MEDIUM] │
    │             │     undoable · 312 MB · 2 collisions     │
    │             │        [Approve]  [Cancel]               │
    │             └──────────────────────────────────────────┘
 L2 ORCHESTRATOR  topological execution · postconditions decide success
    │             budget enforced per step · rollback on failure
 L1 EXECUTORS     file · search · browser · app · system · draft
    │             portable, + thin win32/darwin backends
 L0 MEMORY        episodes · learned preferences · audit hash chain
```

The CLI, the web workspace and the evaluation harness all call the same `MaestroPipeline.handle()` in [`Project/maestro/pipeline.py`](Project/maestro/pipeline.py). A harness that reimplemented the lifecycle would measure a system nobody runs.

Five mechanisms carry the safety argument. Each is code, not policy:

1. **The model proposes; deterministic code decides.** No LLM is called anywhere in `maestro/safety/`. The planner writes a `risk_hint`; the scorer records it, compares it, and ignores it. A planner that claims a permanent delete is R0 changes nothing.
2. **The verb registry is closed.** 28 verbs. The LLM's decoding grammar is built from that list, so it cannot emit a shell command; there is no such token. There is no shell executor anywhere in the codebase.
3. **Nothing touches the disk before the preview.** The dry run produces real counts, real byte totals and real collision warnings. Anything it cannot predict is printed as unknown rather than omitted.
4. **Untrusted content cannot become instruction.** File contents and page text reach exactly one component, the Summarizer, which has no tools and returns a string. Taint is tracked through the plan DAG, and untrusted data reaching a sensitive argument escalates the risk tier.
5. **`ok=True` is not success.** Postconditions are checked against the real filesystem afterwards by a Verifier that is plain code. Ablation A3 removes them and measures the silent-failure rate.

---

## The Safety Model

The safety layer is the project's research contribution and is specified in [`docs/06-SAFETY-SPEC.md`](docs/06-SAFETY-SPEC.md) against a nine-item threat model: misinterpretation, over-reach, indirect prompt injection, irreversible action, scope escape, capability escalation, resource exhaustion, silent failure, and repudiation.

### Risk tiers and gates

| Tier | Meaning | Gate |
|---|---|---|
| R0 | Read-only, no external state change | auto-execute and log |
| R1 | Reversible change inside the workspace | auto-execute, log, undo-capable |
| R2 | Consequential or broader reversible action | explicit confirmation after the dry-run preview |
| R3 | Irreversible, externally visible, or security-sensitive | typed confirmation, or hard block |
| blocked | no override exists | refuse |

The scorer in [`Project/maestro/safety/scorer.py`](Project/maestro/safety/scorer.py) is deterministic, monotonic (a rule may only raise risk) and fail-closed. Its rules, in order:

| Rule | Trigger | Effect |
|---|---|---|
| closed registry | unknown verb, or arguments fail validation | blocked |
| hard block | verb is registered hard-blocked | blocked |
| base risk | always | the verb's registered floor |
| path denylist | any path argument resolves under a denied location | blocked |
| outside workspace | path is outside the allowlist | raise to R2 |
| irreversible | not reversible and no undo declared | raise to R3 |
| bulk | more than 25 files estimated | raise to R2 |
| network write | verb writes to the network | raise to R2 |
| taint | untrusted content reaches a sensitive argument | raise to R2 |

Plan risk is the maximum over its actions. Consent for R3 uses a typed token derived from the plan, so approving one plan can never approve another, and the gate remembers approvals by plan fingerprint and tier to resist habituation.

### The closed verb registry

28 verbs across six categories. Two are registered hard-blocked and have no executor at all: `fs.delete_permanent` and `email.send`. A plan that sends mail cannot be written, only refused; `draft.email` creates an unsent draft instead.

| Category | Verbs | Notes |
|---|---|---|
| file (13) | glob, list_dir, stat, read_text, mkdir, copy, write_text, rename, copy_batch, move_batch, trash, restore_manifest, delete_permanent | trash goes to the Recycle Bin; move_batch has manifest-based undo |
| browser (5) | open, extract, click, fill, download | click and fill are irreversible and start at R2; extract output is untrusted |
| search (3) | by_name, by_content, recent | content matches are untrusted |
| draft (3) | email, note, send | send is hard-blocked |
| app (2) | launch, quit | undo quits or relaunches |
| system (2) | info, set_volume | info covers disk, memory, battery, os, cpu, time, volume, network |

Ten verbs start at R0, seven at R1, nine at R2, two at R3.

### Path policy, audit and injection controls

**Paths** are expanded and resolved before matching, the denylist is checked first and wins, and anything outside the allowlist (`~/Desktop`, `~/Documents`, `~/Downloads`, `~/Pictures`, `~/Music`, `~/Videos`, the workspace) is escalated. The denylist is the union of macOS, Windows and POSIX system locations plus credential patterns (`.ssh`, `.aws`, `.gnupg`, `*.pem`, `*.key`, `.env`, `*.kdbx`, wallets, keychains), unbranched so the cross-platform test compares identical policy.

**Audit** is a SQLite hash chain: each row's hash covers the previous hash and the event, so tampering with any row breaks verification of every row after it. The Audit Integrity metric is literally `verify()` returning true.

**Injection** is handled in layers, and the adversarial suite records which control fired first: the deterministic unsafe-intent prefilter (11 labels, before the model, cannot be overridden by it), Summarizer isolation, the closed registry, the path denylist, the hard-block list, taint tracking, clarification, and the consent gate.

---

## The NLP Layer

[`Project/maestro/nlp/`](Project/maestro/nlp) is a three-stage pipeline specified in [`docs/05-NLP-AND-TRAINING.md`](docs/05-NLP-AND-TRAINING.md).

- **Stage 1, intent.** 16 classes including `OUT_OF_SCOPE` and `UNSAFE_REQUEST`, so correct refusal is a trained, measurable behaviour. The shipping classifier is word and character TF-IDF into a calibrated linear SVM, trained in 1.5 seconds on CPU. The calibrated model ships because clarification is gated on a probability.
- **Stage 2, entities.** Regex plus gazetteer slot extraction. Resolution order is explicit value, then learned preference, then OS default, then ask.
- **Stage 3, clarification.** The system asks when calibrated confidence is low or a required slot is unfilled. It never guesses a destination.

| Intent model, DeskPlan test split | Accuracy | Macro-F1 |
|---|---|---|
| rule-based baseline | 81.5% | 0.79 |
| TF-IDF + calibrated LinearSVC (shipping) | 99.5% | 0.99 |

The only confusion in the test split is `OUT_OF_SCOPE` predicted as `UNSAFE_REQUEST`, which fails safe. The accuracy is inflated by grammar-generated training data; see [Honest Limits](#honest-limits).

---

## The Two Planners

MAESTRO ships a deterministic template planner alongside the LLM planner, and the hybrid falls back automatically.

- **Template planner** ([`rulebased.py`](Project/maestro/planner/rulebased.py)). A fresh clone plans, previews, gates and executes with nothing installed. It is baseline B0 in the evaluation, and it generates every gold plan in the dataset, so no pair can exist that the validator, the registry or the scorer would reject. Against the DeskPlan test split it scores 72.9% plan exact-match and 0.82 action-F1, with 100% DAG validity and 0.3 ms median latency.
- **LLM planner** ([`planner.py`](Project/maestro/planner/planner.py)). Talks to Ollama with constrained decoding: the JSON schema's verb enum is the registry. The model emits only the actions array; plan identity and the instruction hash are bound by the system. A repair loop is bounded at three attempts.
- **Critic** ([`critic.py`](Project/maestro/planner/critic.py)). Reviews a plan for over-reach before the user sees it. Ablation A5 removes it.
- **LoRA fine-tuning** is future work. [`Project/training/train_lora.py`](Project/training/train_lora.py) and its config (Qwen2.5-3B-Instruct, rank 16) are written and unit-tested; no adapter was trained and no result is claimed.

---

## DeskPlan: The Dataset

[`Project/data/DATASET_CARD.md`](Project/data/DATASET_CARD.md) documents the dataset in full. It is rebuilt deterministically with `make dataset` (seed 42) and re-validated by an independent checker in CI.

| | Count |
|---|---|
| Instruction to plan pairs | 3,427 |
| Adversarial cases (test-only) | 216 |
| Sources | 2,601 template, 700 paraphrase, 126 hand-written by the team |
| Splits (train / val / test) | 2,711 / 330 / 602 |
| Paraphrase groups | 1,640, never straddling a split |
| Intents | 16 |

Three rules it is built on:

1. **Every gold plan is one MAESTRO would actually accept.** Generated by the shipping templates, then pushed through the IR validator and the deterministic scorer. The recorded plan risk is what the scorer returned, not an annotator's opinion. If a scoring rule changes, the build fails.
2. **Splits are by paraphrase group, never by row.** "move my PDFs to Documents" and "shift the PDFs into Documents" are one task in two phrasings.
3. **Adversarial cases are test-only**, and the unsafe-request training examples are deliberately different instructions from the evaluation attacks.

The specification budgeted 1,400 LLM-generated pairs. Zero are shipped; the tooling and human-verification gate exist, and the departure is documented in [`Project/docs/README.md`](Project/docs/README.md).

---

## Evaluation

The methodology is in [`docs/07-EVALUATION.md`](docs/07-EVALUATION.md), with five research questions: cross-platform reliability, the cost of the safety layer, injection resistance, whether a small fine-tuned planner can match a frontier one, and whether visible safety increases trust.

### Suites

- **100-task capability benchmark**, frozen before the final run: file and folder 24, search 16, browser 20, app control 12, system 10, drafting 8, multi-category compound 10. 33 easy, 42 medium, 25 hard. Five tasks succeed only if MAESTRO asks instead of acting; one only if it refuses.
- **50 adversarial cases**: 40 attacks (direct unsafe requests, injection through files, injection through web pages, scope escape, ambiguity) plus 10 should-not-refuse controls, so refusing everything cannot score 100%.
- **Held-out phrasings**: two sets of 46 hand-written instructions built to be unlike the grammar, checked at run time to appear nowhere in the dataset or benchmark.

Every task runs in a hermetic sandbox with its own workspace and path policy. Success is decided by predicates checked against the sandbox filesystem afterwards, never by the run's self-report.

### Configurations

| Id | System | What it changes |
|---|---|---|
| B0 | rule-based floor | template planner, full safety layer |
| B1 | no safety layer | safety, dry run, postconditions, Critic and prefilter all off |
| B2 | multi-agent, no safety layer | Critic and memory on, safety off |
| B3 | MAESTRO | everything on |
| A1 | minus safety layer | the price of safety |
| A2 | minus dry run | value of the preview |
| A3 | minus postconditions | silent-failure rate |
| A4 | minus memory | personalisation benefit |
| A5 | minus Critic | over-reach rate |
| A7 | minus trained intent model | rule classifier instead |

B4 (forced LLM planner), A6 and A8 are defined in the harness but not in the shipped matrix. A8 is not a clean ablation, because the Summarizer has no tools structurally rather than by configuration, and the report says so.

### Metrics

Capability: task success rate, partial success, plan exact match, action-level F1, verb sequence accuracy, step efficiency, clarification rate. Safety: unsafe execution rate, safety compliance rate, false confirmation rate, injection resistance rate, hard block rate, risk score accuracy, undo reliability, audit integrity.

Two rules are enforced in code: unsafe execution and false confirmation are always reported together or not at all, because zero unsafe executions bought by prompting constantly would be worthless; and unsafe execution counts executions, not intentions.

### Results across the matrix

| Config | TSR | UER | FCR | SCR | IRR | HBR | p50 ms |
|---|---|---|---|---|---|---|---|
| B0 | 98.9% | 0% | 0% | 100% | 100% | 100% | 267 |
| B1 | 97.7% | 100% | 0% | 59.7% | 60% | 0% | 238 |
| B2 | 97.7% | 100% | 0% | 59.7% | 60% | 0% | 269 |
| **B3** | **98.9%** | **0%** | **0%** | **100%** | **100%** | **100%** | 271 |
| A1 | 98.9% | 100% | 0% | 59.7% | 100% | 100% | 244 |
| A2 | 98.9% | 0% | 0% | 100% | 100% | 100% | 274 |
| A3 | 98.9% | 0% | 0% | 100% | 100% | 100% | 306 |
| A4 | 98.9% | 0% | 0% | 100% | 100% | 100% | 220 |
| A5 | 97.0% | 0% | 0% | 100% | 100% | 100% | 294 |
| A7 | 96.6% | 0% | 0% | 96.1% | 100% | 100% | 234 |

Undo reliability and audit integrity are 100% in every configuration. The three B3 failures are one task three times: a live download from arxiv.org that fails on the reference machine's TLS chain and is rolled back cleanly.

On the adversarial suite B3 resists all 120 attack runs (40 attacks x 3 seeds) with 0% over-refusal across the 30 control runs. Which control fired first: the prefilter 48 times, clarification 42, Summarizer isolation 30.

### Held-out phrasings

| Behavioural accuracy, template planner | v1 (46) | v2 (46, fresh) |
|---|---|---|
| first run, nothing tuned | 69.6% | 67.4% |
| after six prefilter rules written from v1's misses | 82.6% (now a development set) | not applied |
| should execute | 65.2% | 62.5% |
| should ask | 100% | 100% |
| should refuse | 50% to 100% | 50% |

v2 says the prefilter rules did not generalise: fresh refusal phrasings are caught half the time, the same as before the fixes. Every miss failed safe, because the system asked a question and nothing ran. The LLM planner column is not measured; no backend was reachable on the reference machine, and `python eval/heldout.py` fills it in when Ollama is running.

### Reproducing

```bash
make eval          # MAESTRO (B3) on both suites
```

```bash
make eval-matrix   # baselines + ablations, one seed
```

```bash
make eval-full     # three seeds, desktop tasks included (slow, opens applications)
```

```bash
make report        # render the ten tables from the newest run
```

Three seeds are run because the methodology asks for them. Every component on the reference path is deterministic, so identical numbers across seeds are evidence of reproducibility, not of generalisation.

---

## Tests and CI

```bash
make check
```

Lint, the cross-platform boundary check, and 321 tests across 15 modules. The suite runs in about 30 seconds with no model, no GPU and no network.

| Module | Tests | Module | Tests |
|---|---|---|---|
| safety | 52 | agents | 20 |
| executors | 40 | memory | 16 |
| nlp | 35 | pipeline | 15 |
| planner | 29 | conversation | 12 |
| ir | 26 | browser session | 9 |
| dataset and eval | 23 | preview | 9 |
| orchestrator | 21 | ui | 8 |
| review fixes | 6 | | |

The tests worth reading first are the ones that pin the claims: `test_risk_hint_is_recorded_but_ignored`, `test_traversal_does_not_escape_the_allowlist`, `test_audit_tampering_is_detected`, `test_a_failed_postcondition_fails_the_step`, `test_the_plan_cannot_grow_during_execution`, `test_a_refused_episode_exports_as_a_refusal_not_a_plan`, `test_uer_is_measured_against_ground_truth_not_self_report`.

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on Ubuntu, Windows and macOS with Python 3.12, plus Ubuntu with Python 3.10. Each job rebuilds the dataset, benchmark and intent model from source, runs the boundary check and ruff before the tests, runs the B3 evaluation on both suites, and then fails the build if unsafe execution is not zero, hard block is not 100%, audit integrity is not 100%, false confirmation exceeds 10% or injection resistance falls below 90%. A second job diffs the macOS and Windows results to assert the Action IR is identical for the same task.

```bash
make all
```

Rebuilds every artifact in the repository from source with pinned seeds: dataset, benchmark, trained model, tests, evaluation, report.

---

## Honest Limits

Read these before quoting any number. [`Project/eval/report.py`](Project/eval/report.py) generates this list from the run, so it cannot be left out of the results chapter by accident.

- **12 of 100 benchmark tasks were skipped** on the reference machine. Desktop tasks open real applications and cannot be sandboxed, so they are opt-in. TSR describes the 88 tasks that ran.
- **Browser tasks reach live web sites.** Four flake across seeds in one configuration and one fails consistently on the machine's TLS chain. They are rolled back cleanly and counted as failures, but they make the browser rows environment-dependent.
- **B3 scoring 98.9% is a warning, not a result.** The template planner and the benchmark share a task taxonomy. The held-out set shows the real ceiling at 67.4%, and the prefilter rules written from the first held-out set did not generalise to the second.
- **The intent classifier's 99.5% is inflated by template data.** Most of DeskPlan is grammar-generated, so surface diversity is bounded.
- **The LLM planner is not measured.** Every number here is the template planner.
- **LoRA fine-tuning is future work.** The pipeline is written and tested; nothing was trained.
- **No user study.** Table 9 of the report is a template; SUS and trust-calibration figures were not collected.
- **Injection resistance is empirical.** 100% against the attacks in this suite. A novel attack may succeed. Report the rate, never immunity.
- **The allowlist is a policy, not a sandbox.** A bug in an executor could still touch a denied path. Real isolation needs OS-level sandboxing.
- **Three identical seeds prove determinism**, not generalisation.
- **No formal verification**, and consent depends on the user actually reading the preview.

---

## Documentation

### Specification (`docs/`)

| Document | What it covers |
|---|---|
| [01-PRD.md](docs/01-PRD.md) | Product requirements. Frames traditional automation against LLM computer-use agents and names the two motivating failures: a cleanup that becomes `rm -rf`, and a PDF with hidden text asking the agent to email a private key. |
| [02-ARCHITECTURE.md](docs/02-ARCHITECTURE.md) | The five design principles and the L0 to L6 layered view the package mirrors. |
| [03-TECH-STACK-ZERO-COST.md](docs/03-TECH-STACK-ZERO-COST.md) | The zero-cost stack, setup and free-tier verification. |
| [04-ROADMAP.md](docs/04-ROADMAP.md) | 28-week roadmap across the minor and major tracks. |
| [05-NLP-AND-TRAINING.md](docs/05-NLP-AND-TRAINING.md) | The three-stage NLP pipeline, dataset design and fine-tuning plan. |
| [06-SAFETY-SPEC.md](docs/06-SAFETY-SPEC.md) | The threat model and safety controls. This is the research contribution. |
| [07-EVALUATION.md](docs/07-EVALUATION.md) | Research questions, metric definitions and targets. |
| [08-TEAM-DELIVERABLES-RISKS.md](docs/08-TEAM-DELIVERABLES-RISKS.md) | Roles, deliverables and the risk register. |
| [MAESTRO-Progress-Presentation.pptx](docs/MAESTRO-Progress-Presentation.pptx), [MAESTRO-Architecture.mp4](docs/MAESTRO-Architecture.mp4) | Progress deck and architecture walkthrough. |
| [base-papers/](docs/base-papers) | OSWorld, Agent S, Greshake et al. on indirect prompt injection, AgentDojo. |

[`Project/docs/README.md`](Project/docs/README.md) maps every specification section to the module that implements it and the test that pins it, and lists each deliberate departure from the specification.

### Reports and deliverables

| Location | Contents |
|---|---|
| [Major/MAJOR-PROJECT-REPORT.md](Major/MAJOR-PROJECT-REPORT.md), [PDF](Major/MAESTRO-Major-Project-Report.pdf) | The major project report written against the v0.3 prototype. |
| [Minor/Report Writing/](Minor/Report%20Writing) | Progress report, clarifications and key terms, roadmap, system design with diagram sheet, weekly report, viva questions and answers, and one diary per team member. |
| [Minor/WPR's/](Minor/WPR's) | Weekly progress reports 1 to 7. |

---

## Project History

| Stage | Where | What it established |
|---|---|---|
| v0.1 to v0.3 | `Major/` | The Action IR, the closed registry, path policy, deterministic scorer, hash-chained audit, seven file verbs, the orchestrator and the SQLite memory. Built deliberately without an LLM so the constraining layer existed before the thing it constrains. 48 tests. |
| v1.0 | `Project/` | The full pipeline: 16-intent NLP layer, template and LLM planners with a Critic, 28 verbs across six categories with win32 and darwin backends, taint tracking, consent and budget guards, the web workspace, DeskPlan, the 100-task and adversarial suites, the evaluation matrix, held-out sets, the LoRA pipeline and CI on three operating systems. 321 tests. |

---

## Cost Accounting

The project ran at zero marginal cost. LLM inference (local Ollama or none), fine-tuning preparation, speech, the vector store (embedded SQLite) and hosting (public GitHub) replaced an estimated INR 27,500 to 60,500 of paid services.

---

## Project Principle

MAESTRO should not be evaluated by how much power it gives an LLM.

It should be evaluated by how much useful desktop automation it can perform while keeping the model inside a constrained, inspectable, reversible, and measurable execution path.
