# Roadmap — How the Project Gets From Here to Done

**Project MAESTRO** · Group 298 · as of 19 August 2026 (Week 5 of the Minor)

---

## 1. The Two-Track Model

Every week runs two tracks in parallel:

```
        TRACK A — MINOR (graded)                TRACK B — MAJOR (shadow build)
        research · design · report              code · tests · demos
                    │                                       │
                    └──────────────┬────────────────────────┘
                                   ▼
              Enter 8th semester with a validated design
              AND a working system — not a blank repository
```

**The one rule:** if a week is compressed, **Track B slips, Track A does not.** Track B slipping costs comfort next semester; Track A slipping costs marks that cannot be recovered.

---

## 2. Where We Are

```
MINOR (7th sem, 12 weeks)
W1  Title + synopsis            ✅ done (WPR-1)
W2  Setup + lit review begins   ✅ done (WPR-2)  │ B: repo, env, local LLM JSON ✅
W3  Literature review I         ✅ done          │ B: Action IR + registry     ✅
W4  Lit review II + gap         ✅ done (WPR-3/4)│ B: 7 file verbs + dry-run   ✅
W5  Requirements engineering    ◀── YOU ARE HERE │ B: safety engine v0         ✅ (early)
W6  Action IR + risk spec, SCOPE FREEZE          │ B: planner round trip       ✅ (early)
W7  Architecture design                          │ B: orchestrator + memory    ✅ (early)
W8  Diagrams + design doc                        │ B: voice I/O + browser verbs ⏳
W9  Evaluation methodology                       │ B: eval harness, first 20 tasks
W10 Seed dataset (300+ pairs, κ)                 │ B: dataset tooling + retrieval
W11 Report writing (Track B FROZEN)
W12 Polish + viva
```

Track B is running roughly **2–3 weeks ahead** of its own schedule (v0.2 with the LLM planner was a Week-6/7 deliverable; it exists now with 40 passing tests). That buffer is the insurance policy for Weeks 8–10, when the eval harness and dataset tooling compete with heavy report-writing weeks.

---

## 3. Minor — Remaining Weeks in Detail

| Week | Track A (graded) deliverable | Track B (build) deliverable |
|---|---|---|
| **W5 (now)** | Requirements chapter with traceability IDs; 8–10 full-form use cases; use-case diagram; feasibility study | *(already delivered early: safety engine, consent gate, audit chain)* |
| **W6** | **Action IR v1.0 spec · risk taxonomy spec · trust model spec · SCOPE FREEZE** — additions after this week go to Future Work only | *(already delivered early: NL→execution round trip)* — record the 60-second demo capture and show the guide |
| **W7** | Component specs L0–L6; ER model; sequence flows; technology-justification chapter | `search.*` verbs; harden orchestrator edge cases |
| **W8** | **All 10 graded diagrams** (C4 ×3, DFD L0–L1, 3 sequence, state machine, ER, deployment) + algorithm/pseudocode chapter | Voice I/O (faster-whisper in, Piper out); `browser.open/extract/download` via Playwright |
| **W9** | Evaluation methodology chapter: 100-task suite design, formal metrics, baselines B0–B5, ablation matrix, user-study protocol (+ ethics answer) | Eval harness runs first 20 tasks → **first real numbers** for the report |
| **W10** | Dataset spec + annotation guidelines; **300+ seed pairs, 50 adversarial**; Cohen's κ on 50-pair overlap | Dataset validator, template expander, dedup; ChromaDB exemplar retrieval |
| **W11** | **Report assembly only.** Ch. 1–9 from material already written; plagiarism check; guide review with time to act | **Freeze.** Bug fixes + demo video only |
| **W12** | Final formatting; presentation; viva rehearsal (offline demo ×3 on the actual machine); WPR-12 | — |

**Minor exit criteria:** 25+ papers with comparison matrix · frozen IR/risk/trust specs · full architecture + 10 diagrams · fixed evaluation methodology · 350 validated pairs · working vertical-slice demo · report + 12 WPRs.

---

## 4. Major — 16 Weeks (8th Semester)

```
M1–M2   PLATFORM      Windows executor backend (win32/) for all P0 verbs;
                      app/system/draft executors both OSes; differential test green
M3–M4   ROBUSTNESS    Preference learning, workflow retrieval, "the folder I used
                      last time"; re-planning, budget guard, abort/rollback
                      (M4 = designated buffer week)
M5–M6   DATASET       350 → 1,500 → 3,200 pairs (template + LLM-generated,
                      every pair human-verified); 200 adversarial; splits frozen;
                      public release prepared
M7–M8   FINE-TUNE     LoRA on Qwen2.5-3B (MLX local; Kaggle/Colab for sweeps);
                      v2 + hyperparameter sweep + base-model ablations
M9–M10  BENCHMARK     100 tasks × B0–B4 × macOS, then Windows;
                      cross-platform equivalence; ablations A0–A8
M11     ADVERSARIAL   40-case injection suite; per-control attribution table;
                      **UER must be 0**; hard-block verification
M12     USER STUDY    n ≥ 15; SUS + trust + NASA-TLX; interviews; stats
M13     GUI           Electron shell: plan preview, live progress, audit viewer,
                      undo button (M13 = second buffer week)
M14     ANALYSIS      Consolidated results, significance tests, failure taxonomy
M15     WRITING       Major report; IEEE paper draft; dataset + code release
M16     SUBMISSION    Final report, demo video, viva, exhibition
```

**Cut order if more than two weeks slip:** (1) Electron GUI → keep TUI. (2) Windows P1 verbs → macOS-only for app/system, documented as limitation. (3) User study n 20 → 12. **Never cut the adversarial safety evaluation or the fine-tune — they are the contribution.**

---

## 5. How the Finished System Works (the operating loop)

```
                    ┌───────────────────────────────────────────────┐
                    │              DAILY USE (runtime)              │
                    │                                               │
   "move the pdfs   │  NLP → Planner → SAFETY GATE → dry-run        │
    from inbox to ──┼──► preview → consent → execute → verify       │
    archive"        │                    │                          │
                    │                    ▼                          │
                    │        episode recorded (SQLite)              │
                    └────────────────────┬──────────────────────────┘
                                         │ episodes accumulate
                                         ▼
                    ┌───────────────────────────────────────────────┐
                    │           LEARNING LOOP (offline)             │
                    │                                               │
                    │  `learn` exports JSONL candidates             │
                    │      → human review + expected_behavior label │
                    │      → LoRA fine-tune (docs/05)               │
                    │      → swap model via MAESTRO_MODEL           │
                    │      → planner now knows this user's patterns │
                    └───────────────────────────────────────────────┘
```

The runtime loop is the **product**; the learning loop is the **research experiment** (RQ4: can the fine-tuned local 3B match a frontier planner?). The two share one artifact — the episode store — which is why episodes were built into v0.2 now rather than bolted on later.

---

## 6. Milestones & Checkpoints

| Date (approx.) | Milestone | Proof |
|---|---|---|
| ✅ Aug 2026, W4 | Gap analysis + novelty finalized | WPR-4, gap chapter |
| ✅ Aug 2026, W5 | Safety core coded & tested | 40/40 tests, `maestro.cli demo` |
| Sep W6 | **Scope freeze**; specs v1.0; demo shown to guide | Signed-off spec docs + screen capture |
| Sep W8 | Voice-driven demo; 10 diagrams done | Speak → gate → hear result |
| Sep W9 | First real benchmark numbers (20-task pilot) | Metrics CSV |
| Oct W10 | 350 dataset pairs + κ reported | Dataset repo |
| Oct W11–12 | **Minor report + viva** | Submission |
| 8th sem M6 | DeskPlan 3,200 frozen | HF/GitHub release candidate |
| 8th sem M8 | Fine-tune complete | M0–M5 comparison table |
| 8th sem M11 | **UER = 0** on adversarial suite | Per-control attribution table |
| 8th sem M16 | **Final submission** | Report, paper, video, dataset |

---

## 7. Standing Risks to Watch

| Risk | Mitigation already in place |
|---|---|
| Cross-platform doubles the work | Action IR keeps ~80% OS-independent; only L1 executors written twice; CI grep forbids `platform` above L1 |
| Scope creep after W6 | Scope freeze + Future Work file; say it out loud as a team |
| Free cloud tiers vanish | Local models cover every requirement; only the frontier *baseline* is lost |
| Ethics clearance blocks the M12 user study | Question raised with the guide now (Week 5) — long lead time |
| Refusal episodes poisoning the training set | `expected_behavior` labeling is mandatory before any training run (documented known issue) |
| Week-11 report crunch | "Write while reading" discipline since W3; report chapters already exist as drafts |
