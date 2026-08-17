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
| `tests/` | 35 tests incl. the symlink-escape, lying-planner, and audit-tamper cases | — |

## Run it

```bash
cd Major
source .venv/bin/activate     # created with: uv venv --python 3.12 .venv
python -m pytest -q           # 35 passed
python -m maestro.cli demo    # fixture → preview → consent → execute
python -m maestro.cli audit-verify
python -m maestro.cli verbs
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

## Next (per docs/04-ROADMAP.md Track B)

- Week 5–6: planner v0 — local Qwen via Ollama with constrained JSON decoding,
  emitting these same plans from natural language
- then: `search.*` verbs, `browser.*` via Playwright, voice I/O
