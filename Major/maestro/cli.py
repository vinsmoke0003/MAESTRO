"""Minimal CLI — enough to demo the safety pipeline end to end.

    python -m maestro.cli demo          # fixture -> plan -> preview -> consent -> execute
    python -m maestro.cli run PLAN.json # run a hand-written Action IR plan
    python -m maestro.cli verbs         # print the closed verb registry
    python -m maestro.cli audit-verify  # check the audit log hash chain

No LLM yet, deliberately: the planner arrives after this layer is trusted.
Hand-written plans are how the IR gets exercised first (docs/08 §5, item 6).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import maestro.executor  # noqa: F401  (registers verbs + executors)
from maestro import registry
from maestro.ir import Plan
from maestro.orchestrator import Orchestrator, render_preview
from maestro.safety import PathPolicy
from maestro.safety.audit import AuditLog

WORKSPACE = Path("~/maestro_workspace").expanduser()
AUDIT_DB = WORKSPACE / "maestro_audit.db"


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
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    p_run = sub.add_parser("run")
    p_run.add_argument("plan_file")
    p_run.set_defaults(fn=cmd_run)
    sub.add_parser("verbs").set_defaults(fn=cmd_verbs)
    sub.add_parser("audit-verify").set_defaults(fn=cmd_audit_verify)
    ns = ap.parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
