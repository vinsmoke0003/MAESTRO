"""End-to-end: fixture -> plan -> gate -> execute -> verify -> undo/rollback.
Also the audit chain and its tamper-evidence."""

import sqlite3

import pytest

import maestro.executor  # noqa: F401
from maestro.ir import Plan
from maestro.orchestrator import Orchestrator
from maestro.safety import PathPolicy
from maestro.safety.audit import AuditLog


@pytest.fixture
def workspace(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for i in range(3):
        (inbox / f"doc_{i}.pdf").write_text(f"pdf {i}")
    (inbox / "keep.txt").write_text("not a pdf")
    return tmp_path


def move_plan(workspace) -> Plan:
    return Plan.model_validate({
        "plan_id": "t-move",
        "instruction": "move pdfs to archive",
        "actions": [
            {"action_id": "a1", "verb": "fs.glob",
             "args": {"root": str(workspace / "inbox"), "pattern": "*.pdf"},
             "produces": "pdfs"},
            {"action_id": "a2", "verb": "fs.move_batch",
             "args": {"sources": "$pdfs", "dest_dir": str(workspace / "archive")},
             "depends_on": ["a1"]},
        ],
    })


def _test_policy(workspace):
    # pytest tmp dirs live under /var/... on macOS, which the DEFAULT denylist
    # correctly blocks (fail-closed — see test_default_denylist_blocks_tmp).
    # Tests therefore scope both lists to the fixture.
    return PathPolicy(allow_roots=[str(workspace)], deny_dirs=[str(workspace / "deny")])


def orch(workspace, tmp_path, approve: bool):
    return Orchestrator(
        policy=_test_policy(workspace),
        audit=AuditLog(tmp_path / "audit.db"),
        consent=lambda plan, verdict, manifests: approve,
    )


def test_full_run_moves_files(workspace, tmp_path):
    report = orch(workspace, tmp_path, approve=True).run(move_plan(workspace))
    assert report.ok
    archive = workspace / "archive"
    assert sorted(p.name for p in archive.iterdir()) == ["doc_0.pdf", "doc_1.pdf", "doc_2.pdf"]
    assert (workspace / "inbox" / "keep.txt").exists()  # non-PDF untouched


def test_denied_consent_means_zero_side_effects(workspace, tmp_path):
    report = orch(workspace, tmp_path, approve=False).run(move_plan(workspace))
    assert report.status == "denied"
    assert not (workspace / "archive").exists()
    assert len(list((workspace / "inbox").glob("*.pdf"))) == 3


def test_no_consent_callback_fails_closed(workspace, tmp_path):
    o = Orchestrator(policy=_test_policy(workspace), consent=None)
    report = o.run(move_plan(workspace))
    assert report.status == "denied"  # R2 with no way to ask -> deny


def test_default_denylist_blocks_tmp(workspace, tmp_path):
    """With the DEFAULT policy, plans under /var (tmp) are blocked — the
    system-path denylist wins even over an explicit allow_root."""
    o = Orchestrator(policy=PathPolicy(allow_roots=[str(workspace)]),
                     consent=lambda *a: True)
    assert o.run(move_plan(workspace)).status == "blocked"


def test_blocked_plan_never_reaches_executors(workspace, tmp_path):
    plan = Plan.model_validate({
        "plan_id": "t-block",
        "instruction": "permanently delete",
        "actions": [{"action_id": "a1", "verb": "fs.delete_permanent",
                     "args": {"paths": [str(workspace / "inbox" / "doc_0.pdf")]}}],
    })
    report = orch(workspace, tmp_path, approve=True).run(plan)
    assert report.status == "blocked"
    assert (workspace / "inbox" / "doc_0.pdf").exists()


def test_failure_mid_plan_rolls_back(workspace, tmp_path):
    """Step 2 references a nonexistent file -> executed steps are undone."""
    plan = Plan.model_validate({
        "plan_id": "t-rollback",
        "instruction": "move a real file then a ghost",
        "actions": [
            {"action_id": "a1", "verb": "fs.move_batch",
             "args": {"sources": [str(workspace / "inbox" / "doc_0.pdf")],
                      "dest_dir": str(workspace / "archive")},
             "produces": "first"},
            {"action_id": "a2", "verb": "fs.move_batch",
             "args": {"sources": [str(workspace / "inbox" / "GHOST.pdf")],
                      "dest_dir": str(workspace / "archive")},
             "depends_on": ["a1"]},
        ],
    })
    report = orch(workspace, tmp_path, approve=True).run(plan)
    assert report.status == "rolled_back"
    # doc_0 must be back in the inbox after rollback.
    assert (workspace / "inbox" / "doc_0.pdf").exists()


def test_collision_never_overwrites(workspace, tmp_path):
    (workspace / "archive").mkdir()
    (workspace / "archive" / "doc_0.pdf").write_text("ORIGINAL — must survive")
    report = orch(workspace, tmp_path, approve=True).run(move_plan(workspace))
    assert report.ok
    assert (workspace / "archive" / "doc_0.pdf").read_text() == "ORIGINAL — must survive"
    assert (workspace / "archive" / "doc_0 (1).pdf").exists()


def test_audit_chain_records_and_verifies(workspace, tmp_path):
    o = orch(workspace, tmp_path, approve=True)
    o.run(move_plan(workspace))
    assert o.audit.verify()
    events = [r[0] for r in sqlite3.connect(tmp_path / "audit.db")
              .execute("SELECT event FROM audit_log ORDER BY seq")]
    assert events[0] == "PROPOSED"
    assert "GATED" in events and "APPROVED" in events
    assert events.count("EXECUTED") == 2


def test_audit_tampering_is_detected(workspace, tmp_path):
    o = orch(workspace, tmp_path, approve=True)
    o.run(move_plan(workspace))
    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.execute("UPDATE audit_log SET detail = 'nothing happened here' WHERE seq = 2")
    conn.commit()
    conn.close()
    assert AuditLog(tmp_path / "audit.db").verify() is False
