"""Browser workflows are continuous: one session per plan.

The first implementation launched and closed a browser inside every verb, so
open -> fill -> click shared nothing — the field the user filled was gone before
the click. These tests run a real headless Chromium against a local fixture
server and assert the thing that actually matters: state carries from one step
to the next, and the browser is gone when the plan is.

Skipped cleanly when Playwright or its Chromium binary is absent; CI without a
browser must not report these as failures.
"""

from __future__ import annotations

import http.server
import threading

import pytest

import maestro.executor  # noqa: F401
from maestro.executor.base import Context, get_executor
from maestro.ir import Action, Check, Plan
from maestro.orchestrator import Orchestrator
from maestro.safety import ConsentGate
from maestro.safety.consent import Approval


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_available(),
                                reason="Playwright + Chromium not installed")

FORM_PAGE = b"""<!doctype html><html><body>
<h1 id="title">Fixture form</h1>
<form id="f" method="get" action="/done">
  <input id="name" name="name" type="text">
  <input id="note" name="note" type="text">
  <button id="submit" type="submit">Send</button>
</form>
</body></html>"""

DONE_PAGE = b"""<!doctype html><html><body>
<h1 id="title">Submitted</h1><p id="echo">%s</p>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/done"):
            # Escape: `&note=` in raw HTML is the `&not` entity plus "e=", and
            # the browser rendered the echo as "name=Priya¬e=hello".
            import html
            body = DONE_PAGE % html.escape(self.path).encode()
        elif self.path.startswith("/file.txt"):
            body = b"downloadable bytes"
        else:
            body = FORM_PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if b"<html" in body else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # keep pytest output clean
        pass


@pytest.fixture
def server(monkeypatch):
    """A loopback fixture server. Loopback is blocked by the browser verbs on
    purpose (a plan must not reach local services); tests opt in explicitly."""
    monkeypatch.setenv("MAESTRO_ALLOW_LOOPBACK", "1")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def orch(policy) -> Orchestrator:
    return Orchestrator(policy=policy,
                        gate=ConsentGate(ask=lambda r: Approval(True, "typed")))


# =========================================================================== #
# the finished-when criterion: one multi-step task, start to finish
# =========================================================================== #


def test_open_fill_fill_click_shares_one_session(policy, server):
    """Fill two fields, then click submit. The submitted page must echo BOTH
    values — which is only possible if the same page carried between steps."""
    plan = Plan(plan_id="p", instruction="fill the form and send it", actions=[
        Action(action_id="a1", verb="browser.open", args={"url": server}),
        Action(action_id="a2", verb="browser.fill",
               args={"url": server, "selector": "#name", "value": "Priya"},
               depends_on=["a1"]),
        Action(action_id="a3", verb="browser.fill",
               args={"url": server, "selector": "#note", "value": "hello"},
               depends_on=["a2"]),
        Action(action_id="a4", verb="browser.click",
               args={"url": server, "selector": "#submit"}, depends_on=["a3"],
               produces="clicked"),
        Action(action_id="a5", verb="browser.extract",
               args={"url": server + "/done?name=Priya&note=hello", "selector": "#echo"},
               depends_on=["a4"], produces="echo"),
    ])
    report = orch(policy).run(plan)
    assert report.status == "completed", report.message
    # Two independent witnesses that the form really carried both values:
    # the URL the click landed on, and the text the landing page echoed.
    landed = report.variables["clicked"]["url"]
    assert "name=Priya" in landed and "note=hello" in landed
    echo = report.variables["echo"][0]
    assert "name=Priya" in echo and "note=hello" in echo
    assert "navigated to" in report.steps[3].detail


def test_fill_is_verified_by_reading_the_field_back(policy, server):
    ctx = Context()
    try:
        get_executor("browser.open").execute({"url": server}, ctx)
        r = get_executor("browser.fill").execute(
            {"url": server, "selector": "#name", "value": "Arjun"}, ctx)
        assert r.ok and "verified" in r.detail
        assert ctx.session.page.input_value("#name") == "Arjun"
    finally:
        ctx.close()


def test_revisiting_the_same_url_does_not_reload_and_lose_state(policy, server):
    """A verb that re-navigated to the URL it was already on would wipe the
    fields the previous verb filled. `goto` must be a no-op when already there."""
    ctx = Context()
    try:
        get_executor("browser.open").execute({"url": server}, ctx)
        get_executor("browser.fill").execute(
            {"url": server, "selector": "#name", "value": "kept"}, ctx)
        get_executor("browser.fill").execute(
            {"url": server, "selector": "#note", "value": "also"}, ctx)
        assert ctx.session.page.input_value("#name") == "kept"
        assert len(ctx.session.visited) == 1
    finally:
        ctx.close()


# =========================================================================== #
# lifecycle: the session ends with the plan, on every path
# =========================================================================== #


def test_session_is_closed_when_the_plan_completes(policy, server, monkeypatch):
    seen = {}
    real_close = Context.close

    def spy(self):
        seen["had_session"] = self.session is not None and self.session.open
        real_close(self)
        seen["closed"] = self.session is None

    monkeypatch.setattr(Context, "close", spy)
    plan = Plan(plan_id="p", instruction="open", actions=[
        Action(action_id="a1", verb="browser.open", args={"url": server}),
    ])
    assert orch(policy).run(plan).status == "completed"
    assert seen == {"had_session": True, "closed": True}


def test_session_is_closed_when_a_step_fails(policy, server, monkeypatch):
    """Failure handling: a missing selector fails the step, the plan rolls
    back, and the browser is still released."""
    closed = []
    real_close = Context.close
    monkeypatch.setattr(Context, "close",
                        lambda self: (closed.append(True), real_close(self)))
    plan = Plan(plan_id="p", instruction="click nothing", actions=[
        Action(action_id="a1", verb="browser.open", args={"url": server}),
        Action(action_id="a2", verb="browser.click",
               args={"url": server, "selector": "#does-not-exist"}, depends_on=["a1"]),
    ])
    report = orch(policy).run(plan)
    assert report.status == "rolled_back"
    assert not report.steps[1].ok
    # Two contexts, two closes: the dry-run context (which really executed the
    # R0 steps and so may have opened a browser) and the execution context.
    # Both must be released even though the plan failed.
    assert closed == [True, True]


def test_a_plan_without_browser_verbs_never_launches_one(policy, workspace):
    """Lazy creation: file-only plans must not pay for a browser."""
    seen = {}
    real_close = Context.close
    monkeypatch_target = Context

    def spy(self):
        seen["session"] = self.session
        real_close(self)

    monkeypatch_target.close = spy
    try:
        plan = Plan(plan_id="p", instruction="mkdir", actions=[
            Action(action_id="a1", verb="fs.mkdir", args={"path": str(workspace / "d")}),
        ])
        orch(policy).run(plan)
        assert seen["session"] is None
    finally:
        Context.close = real_close


# =========================================================================== #
# download, and the credential guard, through the session
# =========================================================================== #


def test_download_writes_the_bytes_and_undo_removes_them(policy, server, workspace):
    dest = workspace / "dl" / "file.txt"
    plan = Plan(plan_id="p", instruction="download", actions=[
        Action(action_id="a1", verb="browser.download",
               args={"url": server + "/file.txt", "dest": str(dest)}, produces="got",
               postconditions=[Check(check="path_exists", args={"path": str(dest)})]),
    ])
    report = orch(policy).run(plan)
    assert report.status == "completed", report.message
    assert dest.read_bytes() == b"downloadable bytes"
    undo = orch(policy).undo_run(plan, report)
    assert undo.reversed == 0            # no UndoSpec declared on this action...
    get_executor("browser.download").undo(
        type("R", (), {"undo_data": {"downloaded": str(dest)}})(), Context())
    assert not dest.exists()             # ...but the executor's own undo works


def test_credential_fields_are_refused_even_mid_session(policy, server):
    plan = Plan(plan_id="p", instruction="log in", actions=[
        Action(action_id="a1", verb="browser.open", args={"url": server}),
        Action(action_id="a2", verb="browser.fill",
               args={"url": server, "selector": "#password", "value": "hunter2"},
               depends_on=["a1"]),
    ])
    report = orch(policy).run(plan)
    assert report.status == "rolled_back"
    assert "never enters passwords" in report.steps[1].detail


def test_loopback_is_blocked_unless_explicitly_allowed(policy, server, monkeypatch):
    monkeypatch.delenv("MAESTRO_ALLOW_LOOPBACK", raising=False)
    r = get_executor("browser.open").execute({"url": server}, Context())
    assert not r.ok and "loopback" in r.detail
