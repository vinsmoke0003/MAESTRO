# Major Project — Deliverables

This folder holds the **Major-project documents**, the same way `Minor/` holds
the Minor-project ones. **The code is not here; it is in [`../Project/`](../Project).**

| File | What it is |
|---|---|
| [`MAJOR-PROJECT-REPORT.md`](MAJOR-PROJECT-REPORT.md) / [`.pdf`](MAESTRO-Major-Project-Report.pdf) | The first Major-project report, written in August 2026 against the early v0.3 prototype |

## About the old prototype

This folder used to contain the v0.3 prototype (about 2,100 lines, 48 tests):
the first build of the Action IR, closed verb registry, deterministic risk
scorer, path policy, hash-chained audit log, seven file verbs and the
orchestrator. It proved the safety layer worked before any AI was added.

`Project/` grew out of it and replaced it completely (v1.0: 38 verbs, the NLP
layer, the planners, DeskPlan, the evaluation, voice, Google and 467 tests), so
the prototype code was removed to keep one source of truth. It is preserved in
git history under the tag `prototype-v0.3`:

```bash
git checkout prototype-v0.3 -- Major/maestro Major/tests
```

The report above still describes v0.3. Its numbers (48 tests, seven verbs) are
historical; current numbers are in the top-level [`README.md`](../README.md).
