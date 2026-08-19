# Project Progress Report — Where We Stand

**Project MAESTRO** — Design and Evaluation of a Safe Multi-Agent System for Natural Language-Driven Desktop Task Automation
**Group 298** · Shashank Gupta (A2345923073) · Seenu (A2345923074) · Jairaj Berry (A2345923013)
**Guide:** Dr. Rajni Sehgal Kaushik · **Area:** Agentic AI with specialization in NLP
**Institution:** Amity School of Engineering & Technology · B.Tech 7CSE (Evening), Session 2023–27
**Report date:** 19 August 2026 · **Current roadmap position:** Week 5 of 12 (Minor)

---

## 1. Executive Summary

Four weeks in, the project is **on schedule on the graded (Minor) track and ahead of schedule on the build (Major shadow) track**.

- **Minor (research track):** Title finalized, synopsis submitted, literature review Part-I written, a comparative study of existing agent frameworks completed, and the research-gap chapter drafted with a finalized novelty statement. This corresponds exactly to PERT Phases 1–2 (Weeks 1–5) being complete on time.
- **Major (shadow build track):** The safety-first core is not just designed — it is **coded and tested**. MAESTRO v0.2 exists: a working English → plan → safety gate → execution pipeline with **40 passing automated tests**, including adversarial tests (path-traversal escape, a "lying planner," and audit-log tampering).

The single most important status fact: **the research contribution (the deterministic safety layer) already runs on a real machine.** Everything remaining in the Minor semester is specification, evaluation design, and report writing *around* a system that demonstrably works.

---

## 2. What the WPRs Record (Weeks 1–4)

| WPR | Roadmap week | Work recorded | Report chapter produced |
|---|---|---|---|
| **WPR-1** | Week 1 | Project title, objectives, and scope finalized; research domains identified (AI, NLP, Multi-Agent Systems, Desktop Automation, AI Safety); synopsis submitted; feasibility stated (₹0 bill of material — free/open-source stack) | — (5% experimental work) |
| **WPR-2** | Week 2–3 | Studied AI agents, NLP, and Large Language Models; reviewed AI-agent concepts; collected research papers; studied LLM architecture | **Literature Review (Part-I)** |
| **WPR-3** | Week 4 | Studied existing AI desktop-automation frameworks — OpenClaw, Hermes, Open Interpreter — and compared their architectures and capabilities | **Comparative Study section** |
| **WPR-4** | Week 4–5 | Research-gap analysis conducted; shortcomings identified in **transparency, safety, and modular task execution**; project contribution defined; novelty statement finalized | **Research Gap chapter** |

The WPR trail already tells a clean research story: *domain identified → literature surveyed → existing systems compared → gap isolated → contribution stated.* That narrative arc is exactly what the final report's Chapters 1–3 need.

---

## 3. Research Done So Far (Track A — the Minor's substance)

### 3.1 Literature survey (in progress, on schedule)

The survey clusters papers into six threads: computer-use agents, task planning & decomposition, agent safety & guardrails, prompt injection, NL→structured-action datasets, and agent benchmarks. Seed systems mined for citations: **Agent S / Agent S2, OSWorld, TPTU, Voyager**, plus the indirect-prompt-injection literature.

### 3.2 Comparative study of existing systems (WPR-3)

What the comparison of Open Interpreter–class systems (and the OpenClaw/Hermes family of open agent frameworks) established:

| Property | Existing desktop/LLM agents | Consequence |
|---|---|---|
| Capability (open-ended NL) | Strong and improving | Capability is **not** the gap |
| Action representation | Generated code or free-form tool calls | Unanalyzable before execution; unsafe by construction |
| Risk classification | Absent | No principled notion of "dangerous action" |
| Preview / dry-run | Absent or cosmetic | User approves blind |
| Prompt-injection defense | Absent | File/web content can hijack the agent |
| Undo / reversibility | Absent | One wrong plan = permanent loss |
| Audit trail | Plain logs at best | No tamper evidence, no accountability |
| Safety **metrics** | Essentially never reported | Safety cannot even be compared across systems |

### 3.3 The research gap (WPR-4 — the novelty statement)

The four-part defensible gap, each part observable in the literature matrix:

1. **Prior work measures capability, not safety.** There is no accepted safety metric for desktop agents.
2. **Confirmation dialogs are UI; ours is architecture.** A typed intermediate representation (Action IR) with deterministic risk scoring and dry-run has *testable properties*; a popup does not.
3. **No existing desktop-agent work evaluates prompt-injection resistance as a first-class metric.**
4. **No public NL→plan dataset exists** for cross-platform desktop automation with safety annotations. (Ours: **DeskPlan**, 3,000+ pairs target.)

The identified shortcomings — **transparency, safety, modular task execution** — map one-to-one onto MAESTRO's three pillars: the dry-run preview + audit log (transparency), the deterministic risk/consent engine (safety), and the closed verb registry + per-OS executor layer (modularity).

---

## 4. Build Progress (Track B — the Major's head start)

Not graded this semester, but decisive for the 8th semester. Verified today (19 Aug 2026): **40/40 tests pass** in `Major/`.

### v0.1 — the safety-first core (no LLM, deliberately)

| Module | What it does |
|---|---|
| `maestro/ir/` | **Action IR**: typed plans, DAG validation, `$var` dataflow between actions |
| `maestro/registry.py` | **Closed verb registry** — an unknown verb is a rejected plan |
| `maestro/safety/paths.py` | Path allow/denylist with canonicalization (traversal- and symlink-safe) |
| `maestro/safety/scorer.py` | **Deterministic risk scorer** R0–R3 — monotonic, fail-closed, no LLM involved |
| `maestro/safety/audit.py` | Hash-chained, tamper-evident audit log |
| `maestro/executor/fs.py` | 7 portable file verbs, each with `dry_run()` and `undo()` |
| `maestro/orchestrator.py` | dry-run → consent gate → topological execution → rollback |

### v0.2 — the planner and the learning loop

- `maestro ask "<instruction>"` — the full pipeline: English → local LLM (Ollama, **constrained JSON decoding** with the verb enum = the closed registry) → IR validation → deterministic risk scoring → dry-run preview → consent → execution → **episode recorded**.
- `maestro learn` — episode statistics + export of JSONL training candidates for the future LoRA fine-tune.
- Planner schema hardened to force explicit dataflow (`$var` bindings) — the model cannot smuggle implicit state between steps.

### The three properties the test suite pins down (evidence, not claims)

1. **A lying planner cannot downgrade risk** — the planner's `risk_hint` is recorded for a hint-agreement metric but *ignored* for decisions.
2. **Path escapes fail closed** — `workspace/../secrets/x` and symlinks into denied directories are DENIED before matching.
3. **Editing the audit log is detectable** — the hash chain breaks.

### Known issue, documented deliberately

A refused unsafe instruction currently exports as a "completed" episode. Before any training run, every candidate must be human-labeled with `expected_behavior` (execute / clarify / refuse) so **refusals teach refusal, not compliance**. This human-verification step is recorded as non-optional in the dataset design.

---

## 5. Progress vs. the PERT Schedule

| PERT phase | Weeks | Status | Evidence |
|---|---|---|---|
| 1. Literature Review | 1–3 | 🟢 ~80% (finishing to 30 papers) | WPR-2; Literature Review Part-I |
| 2. Existing System Study & Gap Analysis | 4–5 | 🟢 **Complete** | WPR-3, WPR-4; gap chapter drafted |
| 3. Requirement Analysis | 6 | 🟡 **In progress (current week)** | PRD exists (FR-01…FR-62, NFR-01…NFR-10); being formalized into the report chapter |
| 4. System Architecture Design | 7–8 | 🟡 Design done, diagrams pending | Full architecture doc (L0–L6, Action IR, trust model); 10 graded diagrams to produce |
| 5. Module Development | 9–14 | 🟢 **Ahead** — core modules already coded | MAESTRO v0.2, 40 tests |
| 6. System Integration | 15–16 | 🟢 Ahead — end-to-end `ask` pipeline works | CLI demo runs offline |
| 7. Testing & Evaluation | 17–18 | 🟡 Methodology designed; harness pending | Metrics, baselines B0–B5, ablations A0–A8 specified |
| 8. Documentation & Final Report | 19–20 | 🔴 Starting now | This folder |

**Overall Minor progress: ~40% of the semester's graded deliverables; the underlying system is further along than the schedule requires.**

Report-writing status to carry into WPR-5: Literature Review Part-I ✅ · Comparative Study ✅ · Research Gap ✅ · Requirements chapter ⏳ (this week).

---

## 6. What Remains in the Minor (Weeks 5–12)

1. **Week 5 (now):** Requirements chapter with traceability IDs; 8–10 full-form use cases; use-case diagram; feasibility study write-up.
2. **Week 6:** Freeze **Action IR v1.0** spec, risk taxonomy R0–R3, trust model T0–T2. **Scope freeze** — everything proposed later goes to Future Work.
3. **Weeks 7–8:** Architecture chapter (~15 pp); the 10 graded diagrams (C4, DFD, sequence, state machine, ER, deployment); algorithm pseudocode chapter.
4. **Week 9:** Evaluation methodology chapter — 100-task benchmark design, formal metric definitions, baselines, ablations, user-study protocol. *Ethics-clearance question for the user study must be answered by the guide — long lead time.*
5. **Week 10:** 300+ seed instruction→plan pairs; 50 adversarial cases; annotation guidelines; Cohen's κ on a 50-pair overlap.
6. **Weeks 11–12:** Report assembly, plagiarism check, guide review, demo video, viva prep, WPR-12.

---

## 7. How the Minor Feeds the Major

The Minor is the **research**; the Major is the **outcome**. Every Minor deliverable is an input the Major consumes directly:

| Minor produces (research) | Major consumes it to produce (outcome) |
|---|---|
| Literature matrix + gap analysis | The paper's related-work and claims |
| Action IR v1.0 + risk taxonomy specs | The frozen contract all 8th-sem code implements |
| Evaluation methodology (metrics, 100 tasks, ablations) | The benchmark run (3,000 runs) and results chapter |
| 350 seed dataset pairs + guidelines + κ | Scaled to **DeskPlan 3,200** pairs → LoRA fine-tune |
| Working vertical slice (v0.2) | Windows backend, browser/app/system verbs, voice, GUI |
| 12 WPRs | The audit trail of the project itself |

Headline targets the Major must hit: **Unsafe Execution Rate = 0** on the adversarial suite · Injection Resistance ≥ 90% · Task Success ≥ 70% on P0 categories · fine-tuned 3B vs. frontier comparison · user study n ≥ 15 · public dataset release.

*(All terms used above are defined in [02-Clarifications-and-Key-Terms.md](02-Clarifications-and-Key-Terms.md).)*
