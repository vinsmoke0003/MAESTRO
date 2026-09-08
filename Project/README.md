# MAESTRO

**Design and Evaluation of a Safe Multi-Agent System for Natural Language-Driven Desktop Task Automation**

Major project (8th semester) · Group 298 · Amity School of Engineering & Technology
Implementation of the design in [`../docs/`](../docs). Budget: ₹0.

---

## What this is

A desktop assistant that takes an instruction in English and carries it out on a
real computer — and refuses to do it blindly. Every planned action is compiled
into a typed, inspectable intermediate representation, scored for risk by
deterministic code, dry-run before anything touches the disk, gated behind human
confirmation when it crosses a threshold, and written to a tamper-evident audit
log.

The claim is deliberately narrow, because it is the one that can be defended:

> MAESTRO does not make desktop automation *safe*. It makes it **auditable,
> reversible, consent-gated, and injection-resistant** — four properties defined
> operationally and measured.

## The result in one table

Run `full_matrix`, 2026-09-08, this machine, **three seeds**, 88 of 100 tasks
attempted per seed (12 desktop tasks skipped — opt-in, they open real
applications), all 50 adversarial cases:

| | B1 — the naive agent | B3 — MAESTRO |
|---|---|---|
| attempted / completed / failed / skipped (×3 seeds) | 264 / 258 / 6 / 12 | 264 / 261 / 3 / 12 |
| Task Success Rate | 97.7% | 98.9% |
| **Unsafe Execution Rate** | **100%** | **0%** |
| False Confirmation Rate | 0% | **0%** |
| Injection Resistance | 60% | **100%** |
| Hard Block Rate | 0% | **100%** |
| Undo Round-trip (content-verified) | 100% | 100% |
| Audit Integrity | 100% | 100% |

Every number traces to `eval/results/raw/full_matrix_<config>.jsonl`; the full
ten-table report with its generated caveats is
[`eval/results/full_matrix.md`](eval/results/full_matrix.md). The three B3
failures are one task three times: `T3-H-006`, a live download from arxiv.org
that fails on this machine's TLS certificate chain and is rolled back cleanly.
B1's extra failures are the ambiguous instructions MAESTRO turns into
questions and B1 acts on.

B1 is the same executors, the same tasks and the same planner with the safety
layer removed — the system this project would have been without the thesis.
On this suite the safety layer costs **no task success** and takes unsafe
execution from every opportunity to none, **without prompting on a single
low-risk action**.

A safety layer that costs nothing should raise suspicion, and docs/07 §1 says
so: *"if the safety layer costs nothing, you should be suspicious that it isn't
doing anything."* Here the reason is the suite — B3 solves every task it can
reach, so there is no capability left for safety to trade away. The
[held-out phrasings](#the-two-planners) are where the real limits show, and
they are not flattering.

Three seeds are identical for every config except A5, where four live-network
browser tasks flake; the report names them (Table 1b). Identical seeds prove
the reference path is deterministic and reproducible — not that it generalises.

UER and FCR must always be read together: zero unsafe executions bought by
prompting constantly would be worthless. Read the [caveats](#honest-limits)
before quoting any of these.

---

## Quick start

```bash
make install
```

```bash
make demo
```

The demo needs no model, no API key, and no network. It creates a fixture,
plans a real task, shows you the preview, asks for consent, executes, then walks
through a refusal, a clarification and a prompt-injection attempt — and verifies
the audit chain at the end.

```bash
python -m maestro.cli ask "move the pdfs from Downloads to Documents/Invoices"
```

```bash
python -m maestro.cli doctor
```

`doctor` probes what actually works on this machine — it launches Chromium,
loads the intent model, resolves the app registry — rather than reporting what
is importable. Anything it marks MISSING should not be part of a live demo.
Nothing in the core path needs anything beyond `pydantic` and `send2trash`.

```bash
python -m maestro.cli ui
```

The web workspace: one page with the instruction, the action preview (risk
tier, the rules that produced it, dry-run counts), approve / deny, live step
progress, and a task history whose completed runs carry a verified **Undo**.
It calls the same pipeline the CLI calls. `python -m maestro.cli undo` is the
same undo from the terminal.

**On Windows**, `scripts\setup.ps1` does all of the above in one go (venv,
install, dataset, benchmark, intent model, tests, doctor; add `-WithBrowser`
for Chromium), and `scripts\run.ps1 <command>` runs any subcommand without
activating the venv.

### With a local model

MAESTRO plans without an LLM (see [the two planners](#the-two-planners)). To use
one:

```bash
ollama serve && ollama pull qwen2.5:7b-instruct-q4_K_M
```

It is picked up automatically. `MAESTRO_MODEL` switches models; see
[`.env.example`](.env.example).

---

## How it works

```
 "move the pdfs from Downloads to Documents/Invoices"
    │
 L5 NLP           intent (16 classes, calibrated confidence) + entity slots
    │             ├─ unsafe? refuse here, before planning
    │             └─ slot missing or ambiguous? ask, never guess
 L4 PLANNER       LLM (constrained decoding, verb enum = the registry)
    │             or deterministic templates. Repair loop, bounded at 3.
 L3 SAFETY ★      closed verb registry · path allow/denylist · taint tracking
    │             deterministic R0–R3 scorer · budget guard · Critic
    │             DRY RUN — nothing has touched the disk yet
    │             ┌──────────────────────────────────────────┐
    │             │ a1. Find 47 PDFs in ~/Downloads   [safe] │
    │             │ a2. Move 47 files → Invoices    [MEDIUM] │
    │             │     ↩ undoable · 312 MB · 2 collisions   │
    │             │        [Approve]  [Cancel]               │
    │             └──────────────────────────────────────────┘
 L2 ORCHESTRATOR  topological execution · postconditions decide success
    │             budget enforced per step · rollback on failure
 L1 EXECUTORS     file · search · browser · app · system · draft
    │             portable, + thin win32/darwin backends
 L0 MEMORY        episodes · learned preferences · audit hash chain
```

Five things carry the safety argument. Each is a mechanism, not a policy:

**The model proposes; deterministic code decides.** No LLM is called anywhere in
[`maestro/safety/`](maestro/safety). The planner writes a `risk_hint`; the
scorer records it, compares it, and ignores it. A planner that claims a
permanent delete is R0 changes nothing —
`test_risk_hint_is_recorded_but_ignored`.

**The verb registry is closed.** 28 verbs. The planner's decoding grammar is
built *from* that list, so it cannot emit `sys.exec_shell` — there is no such
token. Two verbs (`fs.delete_permanent`, `email.send`) are registered
hard-blocked and have **no executor at all**, so a plan that sends mail cannot
be written, only refused.

**Nothing touches the disk before you see the preview.** The dry run produces
real counts, real byte totals and real collision warnings. Anything it cannot
predict is printed as unknown rather than omitted.

**Untrusted content cannot become instruction.** File contents and page text
reach exactly one component — the Summarizer, which has no tools and returns a
string. There is no code path from it to an action. Taint is tracked through the
plan DAG, and untrusted data reaching a sensitive argument escalates the risk
tier.

**`ok=True` is not success.** Postconditions are checked against the real
filesystem afterwards. Ablation A3 removes them and the silent-failure rate is
the measurement.

---

## The two planners

MAESTRO ships a deterministic template planner alongside the LLM one, and the
hybrid falls back automatically. This is not a stub:

* a fresh clone plans, previews, gates and executes with **nothing installed**;
* it is baseline **B0/M0** in the evaluation — the floor that proves the task
  is not trivial (it scores 85% exact-match on easy tasks, 67% on medium);
* it generates every **gold plan in the dataset**, so no pair can exist that the
  IR validator, the registry or the scorer would reject.

The LLM widens coverage. It is not load-bearing for any safety property.

**Does the template planner generalise?** The benchmark cannot answer that — it
was written from the same task taxonomy as the planner. So
[`eval/heldout.py`](eval/heldout.py) holds 46 instructions written by hand to
be *unlike* the grammar (unfamiliar verbs, hedges, Indian-English filler,
compound requests, missing slots, novel unsafe phrasings), checked at run time
to appear nowhere in the dataset or benchmark. Each is scored on behaviour —
should it execute, ask, or refuse — and every row records which planner
produced the result and whether fallback occurred.

There are two sets, and the distinction is the point:

| behavioural accuracy, template planner | v1 (46) | v2 (46, fresh) |
|---|---|---|
| **held-out score** (first run, nothing tuned) | **69.6%** | **67.4%** |
| after six prefilter rules written from v1's misses | 82.6% *(development set)* | — |
| … should execute (23 / 24) | 65.2% | 62.5% |
| … should ask (11 / 10) | 100% | 100% |
| … should refuse (12 / 12) | 50% → 100% | 50% |
| LLM planner | not measured | not measured |

v1 was held out for exactly one run. Its six refusal misses were then used to
write six prefilter rules, and from that moment v1 is a *development* set: its
82.6% measures how well those rules fit the examples they came from. So a
fresh set, v2, was written afterwards without running it first, evaluated
once, and by rule nothing is fixed because of a v2 miss.

v2 says the rules did **not** generalise: fresh refusal phrasings are caught
50% of the time, the same as before the fixes. Every miss fails safe — the
system asked a question, nothing would have run — but a regex prefilter is
pattern-matching on surface form, and this is what that costs. The "should
execute" misses are the same story from the other side: phrasings the
templates have no shape for (*"how much room is left on this machine"*,
*"crank the volume up to 70"*, *"is the laptop about to run out of battery"*).
That is the honest ceiling of a template planner and exactly what the LLM
planner is for; its column reads *not measured* because no backend was
reachable on the reference machine, and `python eval/heldout.py` fills it in
when Ollama is running. All three runs are in `eval/results/heldout_*.json`.

---

## What is in here

| Path | What |
|---|---|
| [`maestro/ir/`](maestro/ir) | the Action IR — construction *is* validation |
| [`maestro/registry.py`](maestro/registry.py) | the closed verb registry |
| [`maestro/safety/`](maestro/safety) | ★ paths, taint, scorer, consent, budget, audit |
| [`maestro/nlp/`](maestro/nlp) | intents, entities, clarification, safety prefilter |
| [`maestro/planner/`](maestro/planner) | prompts, LLM planner, templates, Critic |
| [`maestro/executor/`](maestro/executor) | 28 verbs + win32/darwin/portable backends |
| [`maestro/agents/`](maestro/agents) | tool-less Summarizer, Verifier |
| [`maestro/orchestrator.py`](maestro/orchestrator.py) | dry-run → gate → execute → verify → roll back |
| [`maestro/pipeline.py`](maestro/pipeline.py) | the whole lifecycle, in one place |
| [`data/`](data) | the DeskPlan dataset + its generator and validator |
| [`eval/`](eval) | 100-task benchmark, 50 adversarial cases, harness, metrics, report |
| [`training/`](training) | intent classifier, LoRA pipeline, planner evaluation |

### DeskPlan — the dataset

3,600+ instruction→plan pairs plus 216 test-only adversarial cases.
[`data/DATASET_CARD.md`](data/DATASET_CARD.md) has the full breakdown.

Three rules it is built on, each enforced by
[`data/validate_dataset.py`](data/validate_dataset.py) and by CI:

1. **Every gold plan is one MAESTRO would actually accept** — generated by the
   shipping template library, then pushed through the IR validator and the
   deterministic scorer. `plan_risk` is not an annotator's opinion; it is what
   the scorer returns. If a scoring rule changes, the build fails.
2. **Splits are by paraphrase group, never by row.** "move my PDFs to Documents"
   and "shift the PDFs into Documents" are one task in two phrasings; splitting
   them across train and test inflates every number.
3. **Adversarial cases are test-only**, and the UNSAFE_REQUEST *training*
   examples are deliberately different instructions from the evaluation attacks.

```bash
make dataset
```

### Evaluation

```bash
make eval          # MAESTRO on both suites
make eval-matrix   # baselines + ablations, three seeds
make report        # the ten tables of docs/07 §7
python eval/heldout.py   # held-out phrasings: template vs LLM planner
```

Three seeds are run because docs/07 §6 asks for them, and the report says
what they show: every component on the reference path (template planner,
scorer, intent model, prefilter) is deterministic, so identical numbers across
seeds are evidence of reproducibility, **not** of generalisation. The held-out
set is where generalisation is measured.

The harness drives the same `MaestroPipeline.handle()` the CLI calls — a harness
that reimplemented the lifecycle would measure a system nobody runs. Every task
runs in a hermetic sandbox with its own workspace and path policy; the real
`~/Downloads` is never touched.

---

## Honest limits

Read these before quoting any number.

* **12 of 100 benchmark tasks were skipped** on the reference machine: the
  desktop tasks (`app.launch` / `app.quit` open real applications and cannot be
  sandboxed, so they are opt-in via `--allow-desktop`). TSR describes the 88
  tasks that ran. Browser tasks *did* run this time, against real Chromium.
* **Browser tasks reach live web sites.** Four of them flake across seeds in
  one config and one (`T3-H-006`) fails consistently on this machine's TLS
  chain. Those are network failures, rolled back cleanly and counted as
  failures — but they make the browser rows environment-dependent. A local
  fixture server would make them reproducible; it is not there yet.
* **B3 scoring 98.9% TSR is a warning, not a result.** The deterministic planner
  and this benchmark were written by the same people from the same task
  taxonomy, so the planner is largely tested on shapes it was built to cover.
  The held-out phrasings show the real ceiling: **67.4%** on a fresh set, and
  the prefilter rules written from the first held-out set did not generalise
  to the second (refusals 50% both times). Every miss failed safe, but a regex
  prefilter recognises surface form, not intent.
* **The intent classifier's 99.5% accuracy is inflated by template data.** Most
  of DeskPlan is grammar-generated, so surface diversity is bounded. The
  hand-written seeds are the antidote and should keep growing.
* **The LLM planner is not measured.** Every number here is the template
  planner; no LLM backend was reachable on the reference machine. The
  comparison the held-out set exists for is one `ollama serve` away.
* **LoRA fine-tuning is future work.** `training/train_lora.py` and its config
  are written and unit-tested; no adapter was trained (needs a GPU) and no
  result is claimed.
* **No user study.** Table 9 of the report is a template; SUS / trust-calibration
  figures were not collected.
* **Injection resistance is empirical.** 100% against the attacks in this suite.
  A novel attack may succeed. Report the rate, never immunity.
* **Ablation A8 is not clean** and the report must say so: MAESTRO cannot run
  without trust isolation, because the Summarizer has no tools *structurally*
  rather than by configuration.
* **Three seeds are identical** for every deterministic config. That is
  reproducibility, not generalisation; the report says so next to the table.
* **The allowlist is a policy, not a sandbox.** A bug in an executor could still
  touch a denied path. Real isolation needs OS-level sandboxing.
* **No formal verification**, and consent depends on the user actually reading
  the preview.

[`eval/report.py`](eval/report.py) generates this list *from the run*, so it
cannot be left out of the results chapter by accident.

---

## Development

```bash
make check
```

Lint, the cross-platform boundary check, and 321 tests. The suite runs in ~30
seconds with no model, no GPU and no network — CI has none of those.

The tests worth reading first are the ones that pin the claims:
`test_risk_hint_is_recorded_but_ignored`,
`test_traversal_does_not_escape_the_allowlist`,
`test_audit_tampering_is_detected`,
`test_a_failed_postcondition_fails_the_step`,
`test_the_plan_cannot_grow_during_execution`,
`test_a_refused_episode_exports_as_a_refusal_not_a_plan`,
`test_uer_is_measured_against_ground_truth_not_self_report`.

```bash
make all
```

Rebuilds every artifact in the repo from source — dataset, benchmark, trained
model, tests, evaluation, report — with pinned seeds. That is NFR-08 as a
command rather than a claim.
