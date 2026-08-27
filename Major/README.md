# MAESTRO — Code (Major Project, Track B)

The implementation of the safety-first core from [docs/02-ARCHITECTURE.md](../docs/02-ARCHITECTURE.md).
No LLM yet, deliberately: the planner only gets added once the layer that
constrains it is built and tested. Hand-written Action IR plans exercise
everything the planner will later produce.

## What exists (v0.1)

| Module | What it is | Spec |
|---|---|---|
| `maestro/ir/` | Action IR: typed plans, DAG validation, `$var` dataflow | docs/02 §3 |
| `maestro/registry.py` | Closed verb registry — unknown verb = rejected plan | docs/06 §1 T6 |
| `maestro/safety/paths.py` | Allow/denylist with canonicalization (traversal & symlink safe) | docs/06 §2 |
| `maestro/safety/scorer.py` | **Deterministic risk scorer** R0–R3, monotonic, fail-closed, no LLM | docs/06 §2 |
| `maestro/safety/audit.py` | Hash-chained tamper-evident audit log | docs/02 §8 |
| `maestro/executor/fs.py` | 7 file verbs, portable, each with `dry_run()` + `undo()` | docs/02 §3.3 |
| `maestro/orchestrator.py` | dry-run → consent gate → topological execute → rollback | docs/02 §4 |
| `tests/` | 48 tests incl. the symlink-escape, lying-planner, and audit-tamper cases | — |

## Run it

```bash
cd Major
source .venv/bin/activate     # created with: uv venv --python 3.12 .venv
uv pip install -e .           # once — puts `maestro` on PATH
python -m pytest -q           # 48 passed

maestro demo                  # fixture → preview → consent → execute
maestro audit-verify
maestro verbs
```

The demo creates `~/maestro_workspace/`, drops fake PDFs in `inbox/`, and runs
a two-step plan (glob → move). You'll see the dry-run preview with real file
counts, approve it, and can then verify the audit hash chain.

## The three properties the tests pin down

1. **A lying planner cannot downgrade risk** — `risk_hint` is recorded for the
   hint-agreement metric and ignored for decisions (`test_risk_hint_is_recorded_but_ignored`).
2. **Path escapes fail closed** — `workspace/../secrets/x` and symlinks into
   denied dirs are DENIED before matching (`test_traversal_does_not_escape_allowlist`,
   `test_symlink_into_denied_dir_is_denied`).
3. **Editing the audit log is detectable** — the hash chain breaks
   (`test_audit_tampering_is_detected`).

## v0.2 — the planner and the learning loop

```bash
maestro ask move the pdfs from inbox to archive
maestro learn     # episode stats + export training candidates
```

`ask` is the full pipeline: English → local LLM (constrained JSON decoding,
verb enum = the closed registry) → IR validation → deterministic risk scoring
→ dry-run preview → consent → execution → **episode recorded**.

The learning loop (how MAESTRO "learns you" over time):

```
use `ask` daily → episodes accumulate → `learn` exports JSONL candidates
   → human review/labeling → LoRA fine-tune (docs/05) → swap in the
   fine-tuned model via MAESTRO_MODEL → planner now knows your patterns → …
```

Model selection: `MAESTRO_MODEL` env var (default `qwen2.5:7b-instruct-q4_K_M`).

## v0.3 — the L0 store

```bash
maestro db        # initialise / inspect the store
maestro review    # label episodes for training
```

One SQLite file (`~/maestro_workspace/maestro.db`) now holds all four L0
tables — `episodes`, `audit_log`, `preferences`, `undo_stack` — because NFR-10
("every executed action traceable to the instruction that caused it") is a join,
and a join needs one database. `schema.migrate()` is additive and idempotent, so
a v0.2 database keeps its history and gains the new columns.

The episode row now carries what the evaluation actually needs: `plan_ms` /
`exec_ms` for the latency percentiles, `consent` alongside `gate` (what the user
did vs. what policy demanded — that difference *is* SCR/FCR), `steps_ok` /
`steps_total`, `input_mode` for the voice-vs-text comparison, and `platform` for
cross-platform equivalence.

**The v0.2 dataset issue is now closed.** Refusals are exported rather than
filtered out — a blocked plan is the only thing that teaches a model to refuse,
so dropping it trained compliance by omission. Every candidate now carries its
`outcome` and a `training_ready` flag that is false until a human sets
`expected_behavior` via `review`. The rule is enforced by the schema, not by
discipline (`tests/test_memory.py`).

## Next (per docs/04-ROADMAP.md Track B)

- `search.*` verbs, then `browser.*` via Playwright
- Week 8: voice I/O — faster-whisper STT in, Piper TTS out, wrapping this
  same pipeline (nothing else changes; `ask` just gains a microphone)
