# MAESTRO

**MAESTRO** stands for **Multi-Agent Execution System for Task Reasoning and Orchestration**.

**Project Title:** Design and Evaluation of a Safe Multi-Agent System for Natural Language-Driven Desktop Task Automation

MAESTRO is a local-first desktop automation research project. It turns natural-language instructions into typed, inspectable action plans, then runs those plans only after deterministic safety checks, dry-run previews, consent gates, rollback support, and tamper-evident audit logging.

The project does not claim to make LLM automation "fully safe." Its research claim is narrower and measurable:

**MAESTRO makes desktop automation auditable, reversible, consent-gated, and injection-resistant.**
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
| Minor Project | Research, design, safety model, evaluation methodology |
| Major Project | Implementation, benchmarking, dataset, fine-tuning, user study |
| Cost Constraint | INR 0 marginal cost, local-first stack |

---

## Why This Exists

Desktop automation today has two weak choices.

Traditional tools such as scripts, macros, and Automator are predictable, but they require programming skill and break when the workflow changes.

LLM computer-use agents understand natural language, but they often execute with broad privileges, provide weak previews, and can be manipulated by untrusted content inside files or web pages.

MAESTRO focuses on the trust gap. The system is designed around a simple rule:

**The model proposes. Deterministic code decides.**

An LLM may generate a plan, but it never decides whether the plan is safe, whether consent is required, or whether a dangerous action may proceed.

---

## Current Status

The repository currently contains both the research documentation and a working Major Project implementation.

> **Where the code is.** The current implementation (v1.0) lives in [`Project/`](Project/) and is documented in
> [`Project/README.md`](Project/README.md), which carries the evaluation results, the quick start and the honest limits.
> `Major/` is the earlier v0.2 prototype that the status tables below describe; it is kept for the record and is not
> the version to run or cite.

### Completed So Far

| Area | Status |
|---|---|
| Project title, scope, and research framing | Done |
| PRD, architecture, roadmap, tech stack, safety spec, evaluation plan | Done |
| Minor report writing material and WPR artifacts | In progress / available |
| Action IR with typed plan validation | Implemented |
| Closed verb registry | Implemented |
| Deterministic R0-R3 risk scorer | Implemented |
| Path allowlist / denylist with canonicalization | Implemented |
| Hash-chained audit log | Implemented |
| File executor verbs with dry-run and undo behavior | Implemented |
| Orchestrator pipeline | Implemented |
| CLI demo, plan runner, audit verifier, verb listing | Implemented |
| Local Ollama planner path | Implemented |
| SQLite L0 store for episodes, audit, preferences, undo stack | Implemented |
| Learning-loop export and human review flow | Implemented |
| Automated tests | 48 passing |

### Current Verification

The Major Project test suite currently passes:

```bash
48 passed
```

The implementation contains approximately 2,719 Python lines including tests.

---

## Repository Layout

```text
.
├── README.md
├── Project/                      # v1.0 — current implementation, evaluation, dataset, training
│   ├── README.md
│   ├── Makefile
│   ├── maestro/
│   ├── data/
│   ├── eval/
│   ├── training/
│   ├── models/
│   └── tests/
├── docs/
│   ├── 01-PRD.md
│   ├── 02-ARCHITECTURE.md
│   ├── 03-TECH-STACK-ZERO-COST.md
│   ├── 04-ROADMAP.md
│   ├── 05-NLP-AND-TRAINING.md
│   ├── 06-SAFETY-SPEC.md
│   ├── 07-EVALUATION.md
│   ├── 08-TEAM-DELIVERABLES-RISKS.md
│   ├── base-papers/
│   └── MAESTRO-Progress-Presentation.pptx
├── Major/                        # v0.2 prototype (superseded by Project/)
│   ├── README.md
│   ├── MAJOR-PROJECT-REPORT.md
│   ├── pyproject.toml
│   ├── maestro/
│   │   ├── cli.py
│   │   ├── orchestrator.py
│   │   ├── registry.py
│   │   ├── executor/
│   │   ├── ir/
│   │   ├── llm/
│   │   ├── memory/
│   │   ├── planner/
│   │   └── safety/
│   └── tests/
└── Minor/
    ├── Report Writing/
    └── WPR's/
```

---

## System Architecture

MAESTRO is organized into layered components.

```text
L6 Interface       CLI now; GUI and voice later
L5 NLP             Intent/entity layer planned
L4 Planner         Local LLM to Action IR
L3 Safety          Schema validation, risk scoring, path policy, consent, audit
L2 Orchestrator    DAG execution, rollback, postcondition handling
L1 Executors       File executor now; search/browser/app/system later
L0 Memory          SQLite episodes, audit, preferences, undo stack
```

The architectural boundary that matters most is the **Action IR**. Natural language is converted into a typed plan before execution. The safety layer validates and scores that plan before any side effect is allowed.

---

## Core Safety Model

MAESTRO uses four risk tiers.

| Tier | Meaning | Policy |
|---|---|---|
| R0 | Read-only, no external state change | Auto-execute and log |
| R1 | Reversible local workspace change | Auto-execute, log, undo-capable |
| R2 | Consequential or broader reversible action | Explicit confirmation |
| R3 | Irreversible, externally visible, or security-sensitive | Typed confirmation or hard block |

Some actions are hard-blocked entirely, including credential entry, purchases, permanent deletion, account creation, CAPTCHA solving, arbitrary shell execution, and administrator elevation.

The safety engine is deterministic. This is intentional: safety decisions must be testable, reproducible, and immune to prompt injection.

---

## Major Implementation

The current implementation (v1.0) lives in `Project/`; the module table below applies to both it and the earlier `Major/` prototype, with `Project/` adding `maestro/nlp/` (intent classifier), `maestro/agents/`, `eval/` (100-task and adversarial suites), `data/` (DeskPlan) and `training/` (LoRA preparation).

### Key Modules

| Module | Purpose |
|---|---|
| `maestro/ir/` | Typed Action IR and DAG validation |
| `maestro/registry.py` | Closed verb registry |
| `maestro/safety/paths.py` | Allowlist and denylist path policy |
| `maestro/safety/scorer.py` | Deterministic risk scoring |
| `maestro/safety/audit.py` | Hash-chained audit log |
| `maestro/executor/fs.py` | File-system verbs with dry-run and undo support |
| `maestro/orchestrator.py` | Dry-run, consent, execution, rollback |
| `maestro/planner/` | Natural-language to Action IR planner |
| `maestro/llm/ollama.py` | Local Ollama client |
| `maestro/memory/` | SQLite schema, episodes, preferences, undo stack |
| `maestro/cli.py` | Developer CLI |

---

## Running The Major Project

From the `Project/` directory:

```bash
make install
```

```bash
make test
```

Run the demo:

```bash
make demo
```

`make help` lists every target (dataset build, benchmark, evaluation matrix, report rendering). Full details are in [`Project/README.md`](Project/README.md).

The earlier `Major/` prototype is run from its own directory with `uv pip install -e .` and `maestro demo`.

Other useful commands:

```bash
maestro verbs
maestro audit-verify
maestro db
maestro ask "move the pdfs from inbox to archive"
maestro learn
maestro review
```

The default workspace is:

```text
~/maestro_workspace
```

It can be changed with:

```bash
MAESTRO_WORKSPACE=/path/to/workspace
```

The default model is:

```text
qwen2.5:7b-instruct-q4_K_M
```

It can be changed with:

```bash
MAESTRO_MODEL=model-name
```

---

## What The Current Demo Shows

The current CLI demo creates a small workspace fixture, finds PDF files, previews the planned effects, asks for approval when needed, executes the file operation, and records the action chain in the audit log.

This already demonstrates the central thesis path:

```text
instruction -> Action IR -> deterministic safety verdict -> dry-run preview -> consent gate -> execution -> audit log
```

---

## Tests

The test suite currently covers the core safety and orchestration behavior, including:

- Action IR validation
- DAG ordering
- closed verb registry behavior
- path traversal denial
- symlink escape denial
- planner risk hints being ignored for decisions
- deterministic risk scoring
- rollback behavior
- audit tamper detection
- SQLite memory schema and training candidate export

Run:

```bash
cd Major
source .venv/bin/activate
python -m pytest -q
```

Expected current result:

```text
48 passed
```

---

## Research Contribution

The project contribution is not simply "an assistant that controls a computer."

The contribution is a safety architecture for desktop agents that can be evaluated through measurable properties:

1. **Auditability**: every proposed, gated, executed, failed, or undone action is logged.
2. **Reversibility**: supported actions declare undo behavior and rollback is part of orchestration.
3. **Consent-gating**: higher-risk plans require user approval after a dry-run preview.
4. **Injection resistance**: untrusted content is treated as data, not instruction, and cannot expand the plan.

These are the properties the report and evaluation should defend.

---

## Still To Build

This table reflects the `Major/` v0.2 prototype. Several of these items (evaluation harness, 100-task benchmark, DeskPlan expansion, LoRA preparation) are delivered in `Project/` v1.0; see the status and limits sections of [`Project/README.md`](Project/README.md) for the current picture.

| Area | Status |
|---|---|
| Search verbs | Not yet implemented |
| Browser verbs via Playwright | Not yet implemented |
| Voice input/output | Not yet implemented |
| TUI or GUI preview | Not yet implemented |
| Evaluation harness | Not yet implemented |
| 100-task benchmark | Not yet implemented |
| DeskPlan dataset expansion | Not yet implemented |
| LoRA fine-tuning | Not yet implemented |
| Windows executor parity | Not yet implemented |
| User study | Not yet implemented |

---

## Recommended Next Steps

1. Update the top-level README from Week 2 orientation to current project status.
2. Keep `Project/README.md` as the developer-focused implementation guide.
3. Keep the root README as the examiner/team-facing overview.
4. Start the next implementation step with `search.*` verbs or the evaluation harness.
5. Keep safety tests central whenever adding a new executor.

---

## Project Principle

MAESTRO should not be evaluated by how much power it gives an LLM.

It should be evaluated by how much useful desktop automation it can perform while keeping the model inside a constrained, inspectable, reversible, and measurable execution path.
