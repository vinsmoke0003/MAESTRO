# 08 — Team, Deliverables & Risk Register

**Project MAESTRO** · v1.0

---

## 1. Role Allocation

You said you're the one building it. That is normal and it is also the biggest risk in this project — not because of workload, but because a viva panel will ask each member what they contributed, and "I helped" is a bad answer for the other two.

The allocation below gives every member **one thing they own end-to-end and can defend alone**, while keeping the integration work with you. That is the shape you want: distinct ownership, low coupling, no one blocked on anyone else.

| Role | Owns | Defends in viva |
|---|---|---|
| **Lead / Architect** *(you)* | Action IR · planner · safety engine · fine-tuning · integration · report editing | The architecture and the safety model |
| **Research & Evaluation** | Literature review · comparison matrix · gap analysis · benchmark task authoring · eval harness · statistics | The research gap and the results |
| **Execution & Platform** | File/browser/app/system executors · **Windows backend** · testing · CI | Cross-platform implementation and testing |
| **Data & Experience** | Dataset annotation · voice I/O · UI · user study · documentation & diagrams | The dataset and the user study |

If you are three rather than four, fold **Data & Experience** into the other two: dataset annotation to Research, voice/UI to Platform. Keep the user study with Research.

### Two rules that protect everyone

1. **Everyone commits code every week**, even small pieces. A git history where one name appears on 95% of commits is a problem for the *other two* at the viva, not for you.
2. **Everyone writes their own chapter.** You edit for consistency. You do not ghost-write it — an examiner can tell, and it puts your own submission at risk.

The instinct to just do everything yourself because it's faster is real, and in the short run it is correct. It is still the wrong call: it converts a group project into a solo project with three signatures, which is a category of problem that gets noticed at exactly the wrong moment.

---

## 2. Deliverables

### Minor project (7th semester)
| # | Deliverable | Due | Owner |
|---|---|---|---|
| D1 | 12 Weekly Progress Reports | Weekly | All |
| D2 | Literature review — 30 papers + matrix | W4 | Research |
| D3 | Gap analysis | W4 | Research |
| D4 | Requirements spec + use cases + traceability | W5 | Lead |
| D5 | **Action IR v1.0 specification** | W6 | Lead |
| D6 | **Risk taxonomy + trust model spec** | W6 | Lead |
| D7 | Architecture document + 10 diagrams | W8 | Lead + Data |
| D8 | Evaluation methodology + 100-task design | W9 | Research |
| D9 | Seed dataset — 350 pairs + guidelines + κ | W10 | Data |
| D10 | **Minor project report** | W11–12 | All |
| D11 | Presentation + demo video | W12 | All |
| D12 | *(Track B)* Working vertical-slice prototype | W6 → W12 | Lead + Platform |

### Major project (8th semester)
Full implementation (both platforms) · 3,200-pair dataset, publicly released · LoRA fine-tuned model + training report · complete benchmark + ablation + adversarial results · user study (n ≥ 15) · Electron GUI · major report · IEEE-format paper draft · demo video · public GitHub release.

---

## 3. Risk Register

Ordered by expected damage. Review the top five every Monday.

| # | Risk | L | I | Mitigation | Early warning sign |
|---|---|---|---|---|---|
| **R1** | **Cross-platform doubles the work and neither platform finishes** | High | High | Action IR boundary + CI grep check ([Arch §7](02-ARCHITECTURE.md#7-cross-platform-strategy)); Windows backend is a whole 8th-sem week (M1); P1 verbs are droppable | Any `sys.platform` outside `executor/` |
| **R2** | **Scope creep** — new features keep arriving | High | High | **Scope freeze in Week 6.** Post-freeze ideas go to `FUTURE_WORK.md`, not the plan | "It'd be cool if it could also…" in Week 9 |
| **R3** | **Track B cannibalizes Track A; report is late and thin** | High | High | The priority rule in [Roadmap](04-ROADMAP.md#how-to-read-this): Track B slips, Track A never does. Weeks 11–12 are code-frozen | Skipping a WPR; report chapters unwritten by W8 |
| **R4** | **Uneven contribution; teammates can't defend their work** | Med | High | §1 ownership; weekly commits from everyone; own chapters | One person's name on most commits |
| **R5** | Report written from scratch in Week 11 | Med | High | Write chapters as work completes, W3 onward | Empty report file in Week 8 |
| **R6** | Ethics clearance blocks the user study | Med | High | **Ask the guide in Week 3.** Fallback: informal usability sessions with informed consent, framed as a pilot | No answer by Week 5 |
| **R7** | Local model too slow/weak for planning | Low | Med | Hybrid router with free cloud fallback (already in the design as a baseline); measure tok/s in Week 2 | p95 planning latency > 15 s |
| **R8** | Free API tier vanishes or is rate-limited during evaluation | Med | Low | Nothing critical depends on cloud; response cache; multiple providers | Quota errors during a run |
| **R9** | macOS permission changes break automation after an OS update | Low | Med | Pin the demo machine's OS version; document the permission set; don't update before the viva | — |
| **R10** | Fine-tune underperforms zero-shot | Med | Low | **This is a valid result.** Report it with failure analysis; classical NLP baselines still stand as contribution | Val loss not improving by epoch 2 |
| **R11** | Dataset annotation takes far longer than planned | Med | Med | Templates do the bulk; LLM-generate + verify; 350 is the real minor-project target, 3,200 is 8th sem across a whole team | < 150 pairs by end of W10 |
| **R12** | Demo fails live in the viva | Low | High | Fully offline architecture; rehearse 3× on the demo machine; **pre-recorded video as backup, always** | Never rehearsed on the actual machine |
| **R13** | Data loss — no backups | Low | High | GitHub from Week 2; dataset + results committed; models excluded but reproducible | Uncommitted work older than a day |
| **R14** | Guide's expectations diverge from this plan | Med | High | Share this document set in Week 2. Get explicit sign-off on the dual-track approach | Vague or hesitant feedback |

R14 is worth acting on this week. Everything here assumes Dr. Kaushik agrees with the dual-track approach and with the safety-first framing. If they want something different, better to learn that in Week 2 than Week 9 — and showing up with a written plan to discuss is itself a strong signal.

---

## 4. Weekly Operating Rhythm

**Monday (45 min, all)** — open the roadmap to the current week; each member reports closed/owned; fill the WPR from the roadmap's deliverable column; update the risk register.

**Wednesday (async)** — push whatever exists. Broken code on a branch beats no code.

**Friday (30 min)** — demo whatever moved. Even a failing test is a demo. Momentum is largely a function of showing each other things.

**Monthly** — 90 minutes with Dr. Kaushik. Bring: what's done, what's next, one decision you want input on. Guides respond well to a specific question and poorly to "any suggestions?"

---

## 5. What To Do In The Next 48 Hours

Ordered. Items 1–3 are the ones that compound.

1. **Fix Python** — `uv` + 3.12. Ten minutes now, three lost days if you skip it.
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. **Create the GitHub repo**, commit these docs, add `.gitignore` and the CI platform-boundary check.
3. **Share this document set with your team and with Dr. Kaushik**, and ask two specific questions: *is the dual-track plan acceptable*, and *does the user study need ethics clearance?*
4. **Set up the shared Zotero library** and drop in the five papers from your synopsis.
5. **Install the stack**, pull the models, measure real tok/s, and get the hello-world JSON call returning `{'ok': True}` ([Tech Stack §6](03-TECH-STACK-ZERO-COST.md#6-week-2-setup-script)).
6. **Write 10 seed instruction→plan pairs by hand.** Do this before writing any planner code. Writing plans manually is how you discover what the Action IR actually needs — a schema designed on paper without this step is always wrong in the same three ways.

---

## 6. The Thing To Remember

You have an unusually strong position: an approved synopsis with a genuine research angle, a year of runway, hardware that makes zero-cost local AI actually work, and a topic you're motivated by. The failure mode for a project like this is not lack of ability or lack of time.

It is spending 24 weeks building an impressive agent and 4 weeks measuring it — and discovering in the final month that you have a demo instead of a result.

The safety layer, the benchmark, and the honest evaluation are the project. The Jarvis-that-talks-to-you is what makes it fun to build and impressive to demo, and you will get it — voice I/O lands in Week 8. But when a week is short and you have to choose, choose the measurement.

Build the thing that can be evaluated. Then evaluate it honestly, including the parts that don't flatter you. That is what turns a very good final-year project into one that could be published.
