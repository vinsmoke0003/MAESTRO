# 04 — Roadmap: 28 Weeks, Two Parallel Tracks

**Project FRIDAY** · v1.0

---

## How To Read This

Every week has **two tracks running simultaneously**:

- **Track A — MINOR (graded).** Research, design, documentation. This is what earns your 7th-semester marks and fills your WPRs. It is non-negotiable and always takes priority when time is short.
- **Track B — MAJOR (shadow build).** Code. Not graded this semester. Its purpose is that you enter 8th semester with a *working system* instead of a blank repository, which is the difference between a comfortable major project and a panicked one.

This split is exactly what you asked for. It works, but only under one rule:

> **If a week is compressed, Track B slips and Track A does not.** Track B slipping costs you comfort in 8th semester. Track A slipping costs you marks you cannot recover. Never invert this — the temptation will be strong around Weeks 9–11 when the code gets interesting and the report is boring.

The **Deliverable** column is written to be paste-able into your WPR.

---

## Semester Map

```
7th SEMESTER (Minor)                          8th SEMESTER (Major)
┌──────────────────────────────┐              ┌──────────────────────────────┐
│ W1   Title, synopsis    ✅   │              │ M1–M2   Executors + Windows  │
│ W2   Setup + lit review ◀ NOW│              │ M3–M4   Memory + personalize │
│ W3   Literature review       │              │ M5–M6   Dataset → 3000       │
│ W4   Gap analysis            │              │ M7–M8   LoRA fine-tune       │
│ W5   Requirements            │              │ M9–M10  Full benchmark run   │
│ W6   Action IR + risk model  │              │ M11     Adversarial safety   │
│ W7   Architecture design     │              │ M12     User study           │
│ W8   Diagrams + design doc   │              │ M13     GUI + polish         │
│ W9   Evaluation methodology  │              │ M14     Analysis + stats     │
│ W10  Seed dataset            │              │ M15     Report + paper       │
│ W11  Report writing          │              │ M16     Viva + submission    │
│ W12  Polish + viva prep      │              └──────────────────────────────┘
└──────────────────────────────┘
```

---

# PART 1 — MINOR PROJECT (7th Semester, 12 Weeks)

---

## Week 1 — ✅ COMPLETE
*Title finalized · Synopsis submitted · Feasibility stated · WPR-1 filed*

---

## Week 2 — Foundation ◀ **YOU ARE HERE** *(Jul 27 – Aug 2)*

### Track A — Minor
| Task | Owner | Detail |
|---|---|---|
| Set up **Zotero** + Better BibTeX, shared group library | All | Every paper goes in here from day one. Retrofitting 30 citations in Week 11 is a full lost day. |
| Collect 30 candidate papers | All | Search terms in Appendix A below |
| Build the literature matrix skeleton | Lead | Columns fixed in Appendix B |
| Read + summarize 6 papers | 2/person | One page each: problem, method, evaluation, limitation, relevance to us |
| **Ask the guide about ethics clearance for the user study** | Lead | Open question Q4 — has lead time, action it now |

### Track B — Major
| Task | Detail |
|---|---|
| Fix Python (3.14b1 → 3.12 via uv) | [Tech Stack §0](03-TECH-STACK-ZERO-COST.md#0-fix-this-first-️) |
| Create GitHub repo, `.gitignore`, CI with the platform-boundary check | [Tech Stack §7](03-TECH-STACK-ZERO-COST.md#7-repository-bootstrap) |
| Install stack; grant macOS permissions | [Tech Stack §5–6](03-TECH-STACK-ZERO-COST.md#5-macos-permissions-do-this-in-week-2) |
| Pull models; **measure real tok/s** → `docs/cost-log.md` | Do not carry my estimates into your report |
| Hello-world: prompt → valid JSON out of local Qwen | The one-liner at the end of Tech Stack §6 |

**Deliverable:** Dev environment operational; 30 papers catalogued; 6 summarized; local LLM returning structured JSON.

---

## Week 3 — Literature Review I *(Aug 3 – 9)*

### Track A
- Read + summarize **12 papers** (4 each), populate the matrix.
- Cluster into: *computer-use agents* · *task planning & decomposition* · *agent safety & guardrails* · *prompt injection* · *NL→structured-action datasets* · *agent benchmarks*.
- Draft §2.1–2.3 of the report as you go. **Write while reading, not after.** The version of you in Week 11 will not remember why paper #17 mattered.

### Track B
- Define the **Action IR** as Pydantic models (`friday/ir/`).
- Implement the **verb registry** with a decorator-based registration pattern.
- Write JSON Schema export + a round-trip test.

**Deliverable:** 18/30 papers reviewed; Action IR v0.1 in code; report §2 drafted to ~5 pages.

---

## Week 4 — Literature Review II & Gap Analysis *(Aug 10 – 16)*

### Track A
- Finish remaining 12 papers → **30 total**.
- Complete the comparison matrix.
- **Write the gap analysis.** This is the single highest-leverage page in the minor report. It must survive: *"Isn't this just Agent S with a confirmation dialog?"*

  Your defensible answer has four parts: (1) prior work measures capability, not safety, and there is no accepted safety metric for desktop agents; (2) confirmation dialogs are UI, whereas a typed IR with deterministic risk scoring and dry-run is an architecture with testable properties; (3) no existing desktop-agent work evaluates prompt-injection resistance as a first-class metric; (4) no public NL→plan dataset exists for cross-platform desktop automation with safety annotations. Support each with citations from your matrix.

### Track B
- Implement the **first executors**: `fs.glob`, `fs.read_text`, `fs.stat`, `fs.mkdir`, `fs.copy`, `fs.move`, `fs.trash` (darwin).
- Implement `dry_run()` for each — do this now, not later; retrofitting dry-run into executors written without it is painful.
- Unit tests against a temp-directory fixture.

**Deliverable:** 30 papers reviewed; comparison matrix complete; gap analysis written; 7 file verbs executing and dry-running with tests.

---

## Week 5 — Requirements Engineering *(Aug 17 – 23)*

### Track A
- Formalize functional + non-functional requirements ([PRD §5–6](01-PRD.md#5-functional-requirements)) into the report's requirement chapter with traceability IDs.
- Write 8–10 **use cases** in full form (actor, preconditions, main flow, alternate flows, postconditions).
- Write the **use-case diagram** and a requirements traceability matrix (requirement → design element → test).
- Feasibility study: technical, economic (use the cost table), operational, schedule.

### Track B
- **Safety engine v0**: schema validation, path allowlist/denylist, deterministic risk scorer.
- Consent gate in the CLI: render a plan preview, capture approve/deny.
- Audit logger with the hash chain.

**Deliverable:** Requirements chapter (~12 pages); use-case diagram; traceability matrix; safety engine gating real file operations.

---

## Week 6 — Action IR & Risk Taxonomy Specification *(Aug 24 – 30)*

This is your most intellectually valuable minor-project week. Protect it.

### Track A
- Formally specify **Action IR v1.0**: full grammar, every field's contract, JSON Schema, worked examples.
- Formally specify the **risk taxonomy**: R0–R3 definitions, the classification rules, the reversibility model, the hard-block list, with justification for each boundary.
- Specify the **trust model** (T0/T1/T2) and the injection-defense rules.
- **Freeze scope here.** Feature list locked. Everything proposed after this week goes in a "Future Work" file, not into the plan. Say this out loud to the team; scope creep after Week 6 is what kills the 8th semester.

### Track B
- **Planner v0**: instruction → Action IR via local Qwen with constrained JSON decoding.
- Schema-repair retry loop (max 3).
- End-to-end: `"move the pdfs in Downloads to Documents"` → plan → gate → preview → execute.

**Deliverable:** Action IR v1.0 spec; risk taxonomy spec; trust model spec; **first fully working NL→execution round trip**.

> When Track B's round trip works, record a 60-second screen capture. Show it to Dr. Kaushik. A guide who has seen your system run in Week 6 assesses everything afterwards differently, and it costs you nothing.

---

## Week 7 — Architecture Design *(Aug 31 – Sep 6)*

### Track A
- Component specification for all layers L0–L6: responsibility, interface, dependencies.
- Data model: full ER diagram, schema DDL, ChromaDB collection design.
- Sequence flows for happy path / denial / failure+rollback / injection blocked.
- Technology justification chapter — every choice from [PRD §8](01-PRD.md#8-key-product-decisions), with the rejected alternative and why.

### Track B
- **Orchestrator**: topological DAG execution, `$var` binding, precondition/postcondition checks, undo stack.
- Failure handling: halt-and-offer-rollback.
- SQLite memory: episodes + audit tables.

**Deliverable:** Architecture chapter (~15 pages); orchestrator executing multi-step DAGs with verification and working undo.

---

## Week 8 — Diagrams & Design Consolidation *(Sep 7 – 13)*

### Track A
- Produce all 10 diagrams from [Architecture §9](02-ARCHITECTURE.md#9-diagrams-to-produce-minor-project-weeks-78). Export SVG.
- Write the **algorithm chapter**: pseudocode for risk scoring, dry-run simulation, DAG scheduling, injection filtering. Examiners like pseudocode; it reads as rigor and it is cheap to produce from working code.
- Internal design review: walk the whole architecture as a team, log every gap found.

### Track B
- **Voice I/O**: faster-whisper STT → pipeline → Piper TTS.
- `browser.open` / `browser.extract` / `browser.download` via Playwright.
- Textual TUI showing plan preview + live step progress.

**Deliverable:** 10 diagrams; algorithm chapter; **voice-driven demo** — speak an instruction, see the gate, hear the result. This is the moment the Iron-Man version of the project becomes real, and it is a strong thing to have in Week 8.

---

## Week 9 — Evaluation Methodology *(Sep 14 – 20)*

### Track A
- Design the **100-task benchmark suite** ([Evaluation §3](07-EVALUATION.md)): categories, difficulty tiers, gold plans, success predicates.
- Define **all metrics** formally, with equations.
- Specify **baselines** (B0–B4) and the **ablation matrix**.
- Design the **user-study protocol**: tasks, SUS, trust scale, NASA-TLX, consent form, n and recruitment. Confirm the ethics answer from Week 2.

### Track B
- Build the **evaluation harness**: task loader, runner, metric computation, CSV output.
- Author the first 20 benchmark tasks with gold plans; run them; get your first real numbers.

**Deliverable:** Evaluation methodology chapter; harness running 20 tasks and emitting a metrics CSV.

> Getting real numbers in Week 9 — even bad ones — is enormously valuable. A minor report that says *"our preliminary implementation achieves 62% task success on a 20-task pilot"* is in a different category from one that only proposes to measure something.

---

## Week 10 — Seed Dataset *(Sep 21 – 27)*

### Track A
- Specify the **dataset**: schema, annotation guidelines, quality criteria, inter-annotator agreement protocol ([Training §2](05-NLP-AND-TRAINING.md)).
- Write **300+ seed instruction→plan pairs** (100 each). Realistic phrasing, including sloppy and ambiguous instructions.
- Write **50 adversarial cases**: unsafe instructions and injection payloads.
- Measure inter-annotator agreement on a 50-pair overlap and report Cohen's κ. Universities love a κ; it is concrete evidence of methodological care.

### Track B
- Dataset tooling: validator, template-expansion generator, deduplication.
- ChromaDB workflow memory + retrieval of exemplars into the planner prompt.

**Deliverable:** 350 validated pairs; annotation guidelines; κ reported; dataset tooling working.

---

## Week 11 — Report Writing *(Sep 28 – Oct 4)*

### Track A — this week is *only* the report
Assemble from work already done. If Weeks 3–10 were written as you went, this is assembly, not composition.

```
1. Introduction            2. Literature Review        3. Gap Analysis
4. Requirements            5. Proposed Architecture    6. Action IR & Safety Model
7. Evaluation Methodology  8. Preliminary Results      9. Conclusion & Future Work
References · Appendices (matrix, use cases, dataset samples, IR schema)
```

- Run a plagiarism check early enough to fix what it finds.
- Get the guide's review with time to act on it. A review you receive two days before submission is a review you cannot use.

### Track B — freeze
Bug fixes and demo-video recording only. **No new features.** This is the week the rule at the top of this document earns its keep.

**Deliverable:** Complete minor report draft; demo video recorded.

---

## Week 12 — Polish & Viva *(Oct 5 – 11)*

### Track A
- Incorporate guide feedback; final formatting; final plagiarism run.
- Build the presentation: problem → gap → architecture → **live demo** → preliminary results → major-project plan.
- **Viva prep — rehearse the hard questions:**
  - *"Isn't this just an LLM with a confirmation popup?"* → the IR, determinism, dry-run, injection isolation. Show the four-control table.
  - *"How do you know it's safe?"* → we don't claim safe; we claim four measured properties. Here are the numbers and the residual risks.
  - *"Why not just use GPT?"* → cost, privacy, offline, and it's a baseline in our evaluation, not a competitor.
  - *"What's actually novel?"* → the four-part gap answer from Week 4.
  - *"What did you personally build?"* → know your own PR history.
- Rehearse the demo **offline, on the actual demo machine, three times**. Wi-Fi in a viva room is a coin flip; your architecture means you don't need it, so prove it.

**Deliverable:** Final report submitted; presentation delivered; WPR-12 filed.

---

# PART 2 — MAJOR PROJECT (8th Semester, 16 Weeks)

You start this with a working vertical slice, a validated architecture, 350 dataset pairs, and a running eval harness. That is roughly a six-week head start.

| Wk | Focus | Key deliverables |
|---|---|---|
| **M1** | Windows executor backend | `win32/` for all P0 verbs; pywinauto; differential test passing on both OSes |
| **M2** | App + system executors | `app.launch/quit`, `sys.info/set_volume`, `draft.email` on both platforms |
| **M3** | Memory & personalization | Preference learning; workflow retrieval; "the folder I used last time" resolution |
| **M4** | Robustness | Re-planning on failure, budget guard, abort/rollback, resource limits |
| **M5** | Dataset scale-up I | Template expansion + LLM generation → 1,500 pairs; human verification pass |
| **M6** | Dataset scale-up II | → 3,000+ pairs; 200 adversarial; final splits frozen; public release prepared |
| **M7** | Fine-tune v1 | LoRA on Qwen2.5-3B via MLX; training curves; first eval vs. zero-shot |
| **M8** | Fine-tune v2 + ablations | Hyperparameter sweep; base-model comparison; quantized inference benchmark |
| **M9** | Benchmark run I | 100 tasks × baselines B0–B4, macOS |
| **M10** | Benchmark run II | Same on Windows; cross-platform equivalence analysis; ablation matrix |
| **M11** | Adversarial safety eval | 40-case injection suite; hard-block verification; **UER must be 0**; per-control attribution table |
| **M12** | User study | n ≥ 15; SUS, trust scale, NASA-TLX; qualitative interviews; statistical analysis |
| **M13** | GUI | Electron shell: plan preview, live progress, audit-log viewer, undo button |
| **M14** | Analysis | All results consolidated; significance tests; figures; failure taxonomy |
| **M15** | Writing | Major report; IEEE-format paper draft; dataset + code release |
| **M16** | Submission | Final report, demo video, viva, project exhibition |

### Buffer policy
M4 and M13 are the designated buffer weeks. If you slip, absorb it there. If you slip more than two weeks total, cut in this order: **(1) Electron GUI → keep the TUI. (2) Windows P1 verbs → macOS-only for app/system control, documented as a limitation. (3) User study n from 20 → 12.** Never cut the adversarial safety evaluation or the fine-tune; those are the contribution.

---

## Appendix A — Literature Search Terms

**Google Scholar · arXiv · ACL Anthology · IEEE Xplore · Semantic Scholar** — all free; use your institutional access for IEEE.

```
"computer use agent"          "GUI agent" LLM
"desktop automation" LLM      "OS agent" benchmark
LLM "task planning" decomposition
"tool use" LLM agent safety
"prompt injection" agent tool
"indirect prompt injection"   LLM agent guardrails
"human in the loop" LLM agent approval
"agent sandboxing" capability
"natural language to action" dataset
"instruction to plan" structured output
"LLM agent evaluation" benchmark
"agent trust" "user study" automation
```

**Seed set (from your synopsis — start here and mine their citations):** Agent S / Agent S2 · OSWorld · TPTU · Voyager. Then follow forward citations on OSWorld and on the injection literature; those two threads are where your gap lives.

**Target mix:** 12 computer-use agents · 6 planning · 8 agent safety & injection · 4 benchmarks & datasets. Prefer 2023–2026. Aim for 25–35 total; more than 40 is padding and reads as such.

---

## Appendix B — Literature Matrix Columns

Fixed schema — do not improvise per paper, or the matrix won't be comparable.

| Column | Values |
|---|---|
| Citation key | Zotero BibTeX key |
| Year / Venue | e.g. 2025 / ICLR |
| System name | — |
| Domain | Desktop / Web / Mobile / Game / General |
| Input modality | Text / Vision / Accessibility tree / Hybrid |
| Action representation | Code / API calls / UI events / Structured IR |
| Planning approach | Single-shot / ReAct / Hierarchical / Multi-agent |
| Model(s) | — |
| Local-capable? | Y / N |
| **Safety mechanism** | None / Confirmation / Sandbox / Policy engine / Formal |
| **Risk classification?** | Y / N |
| **Dry-run / preview?** | Y / N |
| **Injection defense?** | Y / N |
| **Undo / reversibility?** | Y / N |
| **Audit log?** | Y / N |
| Cross-platform? | Y / N — which |
| Benchmark used | OSWorld / WebArena / custom / none |
| Reported metrics | — |
| Safety metrics reported | **Usually "none" — this column is your gap** |
| Limitation (their words) | — |
| Relevance to FRIDAY | 1–5 |

The five bolded columns are the argument. When you fill this matrix and those columns are overwhelmingly "N" while the capability columns are rich, the gap analysis writes itself — and it is defensible because it is *observed*, not asserted.
