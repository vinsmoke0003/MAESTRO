"""The web workspace drives the SAME pipeline the CLI drives.

What these tests pin: the page cannot execute without a preview; a preview
executes nothing; approval is bound to the plan the user looked at; progress is
observable while a run is in flight; the history offers undo only for runs
that can be reversed, and undo is verified. One test goes through the real
HTTP handler so the JSON contract the page depends on is exercised end to end.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import maestro.executor  # noqa: F401
from maestro.nlp import entities as ents
from maestro.pipeline import MaestroPipeline
from maestro.safety import AuditLog
from maestro.ui import Workspace, make_handler


@pytest.fixture
def aliased(workspace):
    """'inbox' and 'archive' resolve inside the test workspace."""
    saved = dict(ents.FOLDER_ALIASES)
    ents.FOLDER_ALIASES["inbox"] = str(workspace / "inbox").replace("\\", "/")
    ents.FOLDER_ALIASES["archive"] = str(workspace / "archive").replace("\\", "/")
    (workspace / "inbox").mkdir(parents=True, exist_ok=True)
    for n in ("a.pdf", "b.pdf"):
        (workspace / "inbox" / n).write_text(f"content of {n}")
    try:
        yield workspace
    finally:
        ents.FOLDER_ALIASES.clear()
        ents.FOLDER_ALIASES.update(saved)


@pytest.fixture
def ws(policy, tmp_path, aliased):
    pipe = MaestroPipeline(policy=policy, audit=AuditLog(tmp_path / "audit.db"))
    w = Workspace(pipe)
    try:
        yield w
    finally:
        w.close()


def _wait(ws: Workspace, timeout: float = 20.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = ws.progress_json()
        if p.get("done"):
            return p
        time.sleep(0.05)
    raise AssertionError("run did not finish")


# --------------------------------------------------------------------------- #


def test_preview_executes_nothing_and_explains_risk(ws, aliased):
    out = ws.plan("move the pdfs from inbox to archive")
    assert out["status"] == "previewed"
    assert (aliased / "inbox" / "a.pdf").exists()          # nothing moved
    assert not (aliased / "archive").exists()
    assert out["risk"] == "R2" and out["gate"] == "confirm"
    verbs = [a["verb"] for a in out["actions"]]
    assert "fs.move_batch" in verbs
    move = next(a for a in out["actions"] if a["verb"] == "fs.move_batch")
    assert move["risk"] == "R2" and move["reasons"]         # the WHY column has content
    assert move["undo"] == "fs.restore_manifest"
    assert any(m["files_touched"] == 2 for m in out["manifests"])
    assert out["token"] is None                              # R2 is a click, not a token


def test_approve_without_preview_is_refused(ws):
    assert "error" in ws.approve()


def test_approve_runs_reports_progress_and_offers_verified_undo(ws, aliased):
    ws.plan("move the pdfs from inbox to archive")
    started = ws.approve()
    assert started.get("started") and started["total"] >= 2
    p = _wait(ws)
    assert p["result"]["status"] == "completed", p["result"]
    assert p["result"]["approval"]["method"] == "click"
    assert [s["ok"] for s in p["steps"]] and all(s["ok"] for s in p["steps"])
    assert not (aliased / "inbox" / "a.pdf").exists()
    assert (aliased / "archive" / "a.pdf").exists()

    hist = ws.history()
    row = next(r for r in hist if r["status"] == "completed")
    assert row["undoable"] is True
    assert all(r["undoable"] is False for r in hist if r["status"] == "previewed")

    undone = ws.undo(row["episode_id"])
    assert undone["ok"], undone["render"]
    assert undone["verified_files"] == 2 and undone["mismatched_files"] == 0
    assert (aliased / "inbox" / "a.pdf").read_text() == "content of a.pdf"
    assert next(r for r in ws.history() if r["episode_id"] == row["episode_id"])["status"] \
        == "undone"
    assert ws.audit()["intact"] is True


def test_deny_discards_the_preview(ws, aliased):
    ws.plan("move the pdfs from inbox to archive")
    assert ws.deny()["ok"]
    assert "error" in ws.approve()
    assert (aliased / "inbox" / "a.pdf").exists()


def test_clarification_round_trip_then_preview(ws):
    out = ws.plan("move my files")
    assert out["status"] == "clarified" and out["clarification"]["question"]
    assert ws.state()["pending"] is not None
    out = ws.answer("cancel")
    assert out["status"] == "cancelled"
    assert ws.state()["pending"] is None


def test_refusal_is_a_terminal_outcome(ws, aliased):
    out = ws.plan("delete everything in inbox permanently")
    assert out["status"] in ("refused", "blocked")
    assert ws.state()["previewed"] is None
    assert (aliased / "inbox" / "a.pdf").exists()


def test_grant_is_bound_to_the_previewed_plan(ws, aliased, monkeypatch):
    """If replanning yields a different plan, the stored approval must not
    apply — the user consented to what they saw."""
    ws.plan("move the pdfs from inbox to archive")
    real = ws.pipe.planner.plan

    def different(instruction, intent=None, slots=None):
        # Same instruction, different effect: the destination moves. The
        # fingerprint is over canonical actions (not the wording), which is
        # exactly why this must be caught.
        plan = real(instruction, intent, slots)
        for a in plan.actions:
            if "dest_dir" in a.args:
                a.args["dest_dir"] = str(aliased / "elsewhere")
        return plan

    monkeypatch.setattr(ws.pipe.planner, "plan", different)
    ws.approve()
    p = _wait(ws)
    assert p["result"]["status"] == "denied"
    # Denied by the pipeline's fingerprint check BEFORE the orchestrator ran,
    # so there is no approval record at all — the gate was never consulted.
    assert "changed between preview and approval" in p["result"]["message"]
    assert p["result"]["approval"] is None
    assert (aliased / "inbox" / "a.pdf").exists()


def test_http_contract(ws, aliased):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ws))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()

        def post(path, body):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())

        status, ctype, body = get("/")
        assert status == 200 and "text/html" in ctype and b"MAESTRO" in body

        status, out = post("/api/plan", {"instruction": "move the pdfs from inbox to archive"})
        assert status == 200 and out["status"] == "previewed"
        assert (aliased / "inbox" / "a.pdf").exists()

        status, out = post("/api/deny", {})
        assert out["ok"]

        status, _, body = get("/api/history")
        assert status == 200 and isinstance(json.loads(body), list)
        status, _, body = get("/api/audit")
        assert json.loads(body)["intact"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()
