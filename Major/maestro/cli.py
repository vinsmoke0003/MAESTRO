"""Minimal CLI — the safety pipeline end to end.

    python -m maestro.cli ask "move the pdfs from inbox to archive"
                                        # natural language -> plan -> gate -> run
    python -m maestro.cli demo          # fixture -> plan -> preview -> consent -> execute
    python -m maestro.cli run PLAN.json # run a hand-written Action IR plan
    python -m maestro.cli verbs         # print the closed verb registry
    python -m maestro.cli audit-verify  # check the audit log hash chain
    python -m maestro.cli learn         # episode stats + export training candidates

`ask` records every interaction as an episode — the learning loop's raw
material (maestro/memory/episodes.py). Model via MAESTRO_MODEL env var,
default qwen2.5:7b-instruct-q4_K_M.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import maestro.executor  # noqa: F401  (registers verbs + executors)
from maestro import registry
from maestro.ir import Plan
from maestro.memory import EpisodeStore, PreferenceStore, UndoStack, schema
from maestro.orchestrator import Orchestrator, render_preview
from maestro.safety import PathPolicy
from maestro.safety.audit import AuditLog

WORKSPACE = Path(os.environ.get("MAESTRO_WORKSPACE",
                                Path("~/maestro_workspace").expanduser()))
# One database for the whole L0 layer. Audit rows must be joinable to the
# episode that produced them (NFR-10), which requires a shared file.
DB = WORKSPACE / "maestro.db"
AUDIT_DB = DB
EPISODES_DB = DB


def _consent(plan, verdict, manifests) -> bool:
    print()
    print(render_preview(plan, verdict, manifests))
    print()
    answer = input("Approve? [y/N] ").strip().lower()
    return answer == "y"


def _orchestrator() -> Orchestrator:
    WORKSPACE.mkdir(exist_ok=True)
    return Orchestrator(
        policy=PathPolicy(),
        audit=AuditLog(AUDIT_DB),
        consent=_consent,
    )


def cmd_demo(_: argparse.Namespace) -> int:
    # Fixture: fake PDFs inside the allowlisted workspace.
    inbox = WORKSPACE / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for i in range(1, 4):
        (inbox / f"invoice_{i}.pdf").write_text(f"fake invoice {i}\n")
    (inbox / "notes.txt").write_text("not a pdf\n")

    plan = Plan.model_validate({
        "plan_id": "demo-001",
        "instruction": "Move the PDFs from inbox to archive",
        "actions": [
            {
                "action_id": "a1",
                "verb": "fs.glob",
                "args": {"root": str(inbox), "pattern": "*.pdf"},
                "produces": "pdf_list",
                "rationale": "Find the PDFs the user referred to",
            },
            {
                "action_id": "a2",
                "verb": "fs.move_batch",
                "args": {"sources": "$pdf_list", "dest_dir": str(WORKSPACE / "archive")},
                "depends_on": ["a1"],
                "produces": "moved",
                "rationale": "Move them to the archive folder",
            },
        ],
    })

    report = _orchestrator().run(plan)
    print(f"\nstatus: {report.status}")
    for s in report.steps:
        print(f"  {s.action_id} {s.verb}: {'ok' if s.ok else 'FAILED'} {s.detail}")
    if report.ok:
        print(f"\nLook in {WORKSPACE / 'archive'} — and check `audit-verify`.")
    return 0 if report.ok or report.status == "denied" else 1


def cmd_run(ns: argparse.Namespace) -> int:
    raw = json.loads(Path(ns.plan_file).read_text())
    plan = Plan.model_validate(raw)  # structural validation happens right here
    report = _orchestrator().run(plan)
    print(f"status: {report.status}")
    for s in report.steps:
        print(f"  {s.action_id} {s.verb}: {'ok' if s.ok else 'FAILED'} {s.detail}")
    return 0 if report.ok else 1


def cmd_ask(ns: argparse.Namespace) -> int:
    from maestro.llm import LLMError, OllamaClient
    from maestro.planner import Planner, PlannerError

    model = os.environ.get("MAESTRO_MODEL", "qwen2.5:7b-instruct-q4_K_M")
    instruction = " ".join(ns.words)
    print(f"planning with {model} ...")
    try:
        plan = Planner(OllamaClient(model=model)).plan(instruction)
    except (LLMError, PlannerError) as e:
        print(f"planning failed: {e}")
        return 1

    report = _orchestrator().run(plan)
    print(f"\nstatus: {report.status}")
    for s in report.steps:
        print(f"  {s.action_id} {s.verb}: {'ok' if s.ok else 'FAILED'} {s.detail}")

    # The learning loop: every interaction becomes an episode.
    store = EpisodeStore(EPISODES_DB)
    store.record(
        instruction=instruction,
        plan_json=plan.model_dump_json(),
        plan_risk=str(report.verdict.risk) if report.verdict else "",
        gate=report.verdict.gate if report.verdict else "",
        status=report.status,
        planner=model,
    )
    store.close()
    return 0 if report.ok or report.status == "denied" else 1


def cmd_learn(_: argparse.Namespace) -> int:
    if not DB.exists():
        print("no episodes yet — use `ask` first")
        return 1
    store = EpisodeStore(DB)
    print("episodes by status:", store.stats())
    out = WORKSPACE / "training_candidates.jsonl"
    r = store.export_dataset(out)
    store.close()
    print(f"exported {r['exported']} episode(s) -> {out}")
    print(f"  training-ready (human-labeled): {r['training_ready']}")
    print(f"  awaiting review:                {r['needs_review']}")
    if r["needs_review"]:
        print("\nRun `maestro review` to label them. Nothing enters training")
        print("unverified — refusals must teach refusal (docs/05 §2).")
    return 0


def cmd_review(ns: argparse.Namespace) -> int:
    """Human labeling queue — the verification step docs/05 §2 makes mandatory."""
    if not DB.exists():
        print("no episodes yet — use `ask` first")
        return 1
    store = EpisodeStore(DB)
    queue = store.unlabeled(limit=ns.limit)
    if not queue:
        print("nothing to review — every episode is labeled.")
        store.close()
        return 0
    print(f"{len(queue)} episode(s) awaiting review "
          f"(refusals first). Enter = skip, q = quit.\n")
    labels = ["execute_auto", "execute_with_consent", "clarify", "refuse"]
    for item in queue:
        print(f"  #{item['episode_id']}  [{item['status']}/{item['plan_risk']}]"
              f"  \"{item['instruction']}\"")
        if item["suggested"]:
            print(f"      suggested: {item['suggested']}")
        for i, lab in enumerate(labels, 1):
            print(f"      {i}) {lab}")
        choice = input("      label> ").strip().lower()
        if choice == "q":
            break
        if choice.isdigit() and 1 <= int(choice) <= len(labels):
            store.label(item["episode_id"], labels[int(choice) - 1])
            print(f"      -> {labels[int(choice) - 1]}\n")
        else:
            print("      skipped\n")
    store.close()
    return 0


def cmd_db(_: argparse.Namespace) -> int:
    """Initialise / inspect the L0 store."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    conn = schema.connect(DB)
    added = schema.migrate(conn)
    print(f"database: {DB}")
    if added:
        print(f"migrated, added: {', '.join(added)}")
    print()
    for table in ("episodes", "audit_log", "preferences", "undo_stack"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            print(f"  {table:<12} {n:>6} row(s)   {len(cols)} columns")
        except Exception:
            print(f"  {table:<12} (not created yet — written on first use)")
    prefs = PreferenceStore(conn).all()
    if prefs:
        print("\n  preferences:")
        for p in prefs:
            print(f"    {p['key']} = {p['value']}  (from {p['learned_from']})")
    pending = UndoStack(conn).pending()
    if pending:
        print(f"\n  {len(pending)} un-applied undo entr(ies) — `maestro undo` to replay")
    conn.close()
    return 0


def cmd_verbs(_: argparse.Namespace) -> int:
    for v in registry.known_verbs():
        spec = registry.get(v)
        flags = " HARD-BLOCKED" if spec.hard_blocked else ""
        print(f"  {v:<22} base={spec.base_risk}{flags}  {spec.description}")
    return 0


def cmd_audit_verify(_: argparse.Namespace) -> int:
    if not AUDIT_DB.exists():
        print("no audit log yet — run the demo first")
        return 1
    ok = AuditLog(AUDIT_DB).verify()
    print("audit chain: " + ("VALID — log has not been tampered with" if ok else "BROKEN"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maestro")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ask = sub.add_parser("ask")
    p_ask.add_argument("words", nargs="+", help="natural-language instruction")
    p_ask.set_defaults(fn=cmd_ask)
    sub.add_parser("learn").set_defaults(fn=cmd_learn)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    p_run = sub.add_parser("run")
    p_run.add_argument("plan_file")
    p_run.set_defaults(fn=cmd_run)
    p_review = sub.add_parser("review", help="label episodes for training")
    p_review.add_argument("--limit", type=int, default=20)
    p_review.set_defaults(fn=cmd_review)
    sub.add_parser("db", help="initialise / inspect the L0 store").set_defaults(fn=cmd_db)
    sub.add_parser("verbs").set_defaults(fn=cmd_verbs)
    sub.add_parser("audit-verify").set_defaults(fn=cmd_audit_verify)
    ns = ap.parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
