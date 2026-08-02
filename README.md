# Project FRIDAY

**Official Title:** Design and Evaluation of a Safe Multi-Agent System for Natural Language-Driven Desktop Task Automation

**Codename:** FRIDAY — *Framework for Reliable, Instruction-Driven Automation with Yielding-to-human oversight*

| | |
|---|---|
| Institution | Amity School of Engineering & Technology |
| Programme | B.Tech CSE (Evening), Session 2023–27 |
| Group No. | 298 |
| Guide | Dr. Rajni Sehgal Kaushik |
| Area | Agentic AI with specialization in Natural Language Processing |
| Team | Shashank Gupta (A2345923073), Seenu (A2345923074), Jairaj Berry (A2345923013) |
| Minor Project | 7th Semester — 12 weeks — research, design, evaluation methodology |
| Major Project | 8th Semester — 16 weeks — implementation + experimental evaluation |
| Budget | **₹0.** Hard constraint. See [03-TECH-STACK-ZERO-COST.md](docs/03-TECH-STACK-ZERO-COST.md) |

---

## You Are Here

```
Week 1  ✅ DONE — Title finalized, synopsis submitted, WPR-1 filed
Week 2  ◀── YOU ARE HERE (Minor Track A: literature review begins)
                                (Major Track B: repo + environment + first LLM call)
```

Both tracks run **in parallel from this week**. That was your explicit ask: don't waste 7th semester waiting.

---

## Read These In Order

| # | Document | What it answers | Read when |
|---|---|---|---|
| 1 | [PRD](docs/01-PRD.md) | What are we building, for whom, what's in and out of scope | **Now.** Read fully. |
| 2 | [Architecture](docs/02-ARCHITECTURE.md) | How the system is structured; the Action IR that makes cross-platform possible | **Now.** This is the technical core. |
| 3 | [Zero-Cost Tech Stack](docs/03-TECH-STACK-ZERO-COST.md) | Every API/tool, its free-tier limits, and the setup commands | **Now.** Do the setup this week. |
| 4 | [Roadmap](docs/04-ROADMAP.md) | Week-by-week plan for both tracks, 28 weeks total | **Now.** Then re-read every Monday. |
| 5 | [NLP & Training](docs/05-NLP-AND-TRAINING.md) | The dataset you'll build and the LoRA fine-tune — your ML contribution | Week 4 onward |
| 6 | [Safety Spec](docs/06-SAFETY-SPEC.md) | The risk taxonomy and policy engine — **this is your research novelty** | Week 5 onward |
| 7 | [Evaluation](docs/07-EVALUATION.md) | Metrics, benchmark suite, baselines, ablations, user study | Week 8 onward |
| 8 | [Team, Deliverables & Risks](docs/08-TEAM-DELIVERABLES-RISKS.md) | Who does what, what gets submitted, what could go wrong | **Now.** Share with the team. |

---

## The One-Paragraph Version

You are building a desktop AI assistant that takes natural-language instructions ("archive last month's invoices and email me a summary") and executes them on a real computer. Dozens of projects do that. **Yours is different because it refuses to do it blindly**: every planned action is compiled into a typed, inspectable intermediate representation, scored for risk and reversibility, dry-run before execution, gated behind human confirmation when it crosses a threshold, and written to a tamper-evident audit log. You will build your own instruction→plan dataset, fine-tune a small open model on it, and prove with numbers that the safety layer costs you almost nothing in capability while eliminating an entire class of failures — including prompt-injection attacks that hijack the agent through file contents and web pages.

That last sentence is your paper. Everything else is engineering in service of it.

---

## Three Things To Internalize Before You Start

**1. The safety layer is the project, not a feature.**
If you build a great agent with a weak safety layer, you have a worse version of something that already exists. If you build a mediocre agent with a rigorous, measurable safety layer, you have a contribution. Every time you must choose where to spend a week, spend it on the safety/evaluation side.

**2. "Fully safe" is not a claim you can defend — and you should not try.**
An LLM-driven agent with filesystem and browser access cannot be proven safe. If you write "fully safe" in your report, your examiner will take it apart in the viva. What you *can* defend, with evidence, is: **auditable, reversible, consent-gated, and injection-resistant**. Those are four measurable properties. Claim exactly those, show the numbers, and name the residual risks yourself before anyone else does. Owning the limitation is what separates a research report from a product pitch. This is written up properly in [06-SAFETY-SPEC.md](docs/06-SAFETY-SPEC.md#what-we-do-not-claim).

**3. Cross-platform is the single biggest threat to your timeline.**
You chose Windows + macOS. That is defensible and it strengthens the report — but it is also how final-year projects die. The mitigation is architectural, not managerial: the **Action IR** (see [Architecture](docs/02-ARCHITECTURE.md#3-the-action-ir)) keeps ~80% of the system OS-independent, and only a thin executor layer is written twice. Guard that boundary. The moment platform-specific logic leaks upward into the planner, you have two projects instead of one.

---

## Repository Layout (to be created in Week 2)

```
friday/
├── docs/                    # These documents + generated diagrams
├── friday/
│   ├── nlp/                 # Intent classification, entity extraction
│   ├── planner/             # LLM planner → Action IR
│   ├── safety/              # Policy engine, risk scoring, dry-run  ★ novelty
│   ├── executor/
│   │   ├── base.py          # OS-agnostic interfaces
│   │   ├── darwin/          # macOS backend
│   │   └── win32/           # Windows backend
│   ├── memory/              # SQLite (episodic) + ChromaDB (semantic)
│   ├── voice/               # Whisper STT, Piper TTS, wake word
│   └── llm/                 # Provider router (local Ollama ↔ free cloud)
├── data/
│   ├── seed/                # Hand-written instruction→plan pairs
│   ├── generated/           # Template + LLM-expanded pairs
│   └── adversarial/         # Injection & unsafe-instruction test cases
├── eval/
│   ├── tasks/               # 100-task benchmark suite
│   ├── harness.py           # Automated runner
│   └── results/             # CSVs + plots for the report
├── training/                # LoRA fine-tuning scripts + configs
└── ui/                      # Electron shell (built last, in 8th sem)
```

---

## Weekly Ritual (non-negotiable, 45 minutes every Monday)

1. Open [04-ROADMAP.md](docs/04-ROADMAP.md), find the current week, read both tracks.
2. Each member states what they closed last week and what they own this week.
3. Fill the WPR **from the roadmap**, not from memory. The roadmap's deliverable column is written to be paste-able into the WPR form.
4. Log any slip in the risk table in [08-TEAM-DELIVERABLES-RISKS.md](docs/08-TEAM-DELIVERABLES-RISKS.md). A slip you have written down is a managed risk; a slip you remember is a surprise in Week 11.

The WPR is not bureaucracy. Twenty filed WPRs that trace a clean line from literature review to results *is* the narrative your report needs, and it is the cheapest marks in the entire degree.
