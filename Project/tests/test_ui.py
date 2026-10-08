"""The web workspace drives the SAME pipeline the CLI drives.

What these tests pin: the page cannot execute without a preview; a preview
executes nothing; approval is bound to the plan the user looked at; progress is
observable while a run is in flight; the history offers undo only for runs
that can be reversed, and undo is verified. One test goes through the real
HTTP handler so the JSON contract the page depends on is exercised end to end.
"""

from __future__ import annotations

import json
import re
import threading
import time
import types
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import maestro.executor  # noqa: F401
from maestro import ui
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

        status, ctype, body = get("/api/integrations")
        google = json.loads(body)["google"]
        assert status == 200 and "application/json" in ctype
        assert google["state"] in ("setup_required", "connect_required")   # nothing set up
        assert set(google["services"]) == {"gmail", "drive", "calendar"}
    finally:
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------- #
# the desktop shell (ui.html): static checks on the page itself
# --------------------------------------------------------------------------- #

PAGE_HTML = (Path(ui.__file__).parent / "ui.html").read_text(encoding="utf-8")
SCRIPT = PAGE_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
NAV = ["assistant", "connections", "voice", "activity", "settings"]


class _Ids(HTMLParser):
    """Collects element ids, nav targets, sections, and controls per section."""

    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.nav: list[str] = []
        self.nav_links: list[dict] = []
        self.sections: list[str] = []
        self.section: str | None = None
        self.depth = 0
        self.controls: dict[str, list[dict]] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a" and "data-page" in a:
            self.nav.append(a["data-page"])
            self.nav_links.append(a)
        if tag == "section" and "data-section" in a:
            self.section, self.depth = a["data-section"], 0
            self.sections.append(self.section)
        elif tag == "section" and self.section:
            self.depth += 1
        if self.section and tag in ("button", "input", "select", "textarea"):
            self.controls.setdefault(self.section, []).append(a)

    def handle_endtag(self, tag):
        if tag == "section" and self.section:
            if self.depth:
                self.depth -= 1
            else:
                self.section = None


@pytest.fixture(scope="module")
def page():
    p = _Ids()
    p.feed(PAGE_HTML)
    return p


def test_shell_has_the_five_pages_each_reachable_from_the_sidebar(page):
    assert page.nav == NAV
    assert sorted(page.sections) == sorted(NAV)


def test_every_nav_link_has_an_accessible_name(page):
    """Below 760px the inactive labels are hidden, so the links rely on aria-label."""
    for key, link in zip(NAV, page.nav_links, strict=True):
        name = key.capitalize()
        assert link.get("aria-label") == name
        assert link.get("title", "").startswith(name)


def test_sidebar_footer_does_not_show_the_pipeline_or_paths():
    assert "$('pipeline').textContent = s.pipeline" not in SCRIPT
    assert "Local-first" in PAGE_HTML
    assert "$('set-pipeline').textContent = s.pipeline" in SCRIPT   # Settings keeps it


def test_every_element_the_script_uses_exists(page):
    used = set(re.findall(r"\$\('([\w-]+)'\)", SCRIPT))
    used |= set(re.findall(r"(?:pill|show)\('([\w-]+)'", SCRIPT))
    assert used, "the script should reference elements"
    assert used <= page.ids, f"missing ids: {sorted(used - page.ids)}"


def test_page_calls_only_the_existing_endpoints():
    """No second pipeline: the shell talks to exactly the API ui.py already serves."""
    served = set(re.findall(r'"(/api/[\w/]+)"', Path(ui.__file__).read_text(encoding="utf-8")))
    called = set(re.findall(r"'(/api/[\w/]+)'", SCRIPT))
    assert called <= served, f"unknown endpoints: {sorted(called - served)}"
    assert {"/api/plan", "/api/answer", "/api/approve", "/api/deny", "/api/progress",
            "/api/history", "/api/audit", "/api/undo", "/api/state"} <= called


def test_history_audit_and_undo_live_on_the_activity_page():
    activity = PAGE_HTML.split('data-section="activity"', 1)[1].split("</section>", 1)[0]
    for el in ('id="history"', 'id="audit"', 'id="btn-undo-last"'):
        assert el in activity
    assistant = PAGE_HTML.split('data-section="assistant"', 1)[1].split("</section>", 1)[0]
    for el in ('id="instruction"', 'id="card-clarify"', 'id="card-preview"',
               'id="btn-approve"', 'id="btn-deny"', 'id="card-progress"'):
        assert el in assistant


def test_placeholder_pages_have_no_live_controls(page):
    for name in ("settings",):
        live = [c for c in page.controls.get(name, []) if "disabled" not in c]
        assert not live, f"{name} has an enabled control: {live}"


def _fn(name: str) -> str:
    """The source of one top-level function in the page script."""
    m = re.search(r"(?:async )?function " + name + r"\(.*?\n}\n", SCRIPT, re.S)
    assert m, name
    return m.group(0)


def test_connections_page_controls_start_safe(page):
    """Connect / Disconnect start disabled; the status decides when they turn on."""
    ctl = {c.get("id"): c for c in page.controls.get("connections", [])}
    assert "disabled" in ctl["btn-google-connect"] and "disabled" in ctl["btn-google-disconnect"]
    live = sorted(i for i, c in ctl.items() if "disabled" not in c)
    assert live == ["btn-google-cancel", "btn-google-confirm", "btn-google-refresh"]
    assert 'role="alertdialog"' in PAGE_HTML and "confirm(" not in SCRIPT   # no native dialog


def test_connections_reads_use_get_and_never_start_sign_in():
    assert re.findall(r"'(/api/[\w/]+)'", _fn("loadIntegrations")) == ["/api/integrations"]
    watch = _fn("watchFlow")
    assert re.findall(r"'(/api/[\w/]+)'", watch) == ["/api/integrations/google/flow"]
    for name in ("loadIntegrations", "watchFlow", "renderGoogle", "updateGoogleButtons", "go",
                 "boot"):
        assert "protectedPost(" not in _fn(name) and "connectGoogle()" not in _fn(name)
    assert "connectGoogle(" not in SCRIPT.replace("async function connectGoogle(", "")


def test_only_state_changing_actions_send_the_request_token():
    assert SCRIPT.count("protectedPost(") == 4        # the definition + three callers
    assert "protectedPost('/api/integrations/google/connect')" in _fn("connectGoogle")
    assert "protectedPost('/api/integrations/google/disconnect')" in _fn("confirmDisconnect")
    assert "protectedPost(path)" in _fn("voiceAction")
    assert SCRIPT.count("X-MAESTRO-Token") == 1
    for fn, path in (("startVoice", "/api/voice/start"), ("stopVoice", "/api/voice/stop"),
                     ("discardVoice", "/api/voice/discard")):
        assert f"voiceAction('{path}')" in _fn(fn)
    assert set(ui.PROTECTED_POSTS) == {
        "/api/integrations/google/connect", "/api/integrations/google/disconnect",
        "/api/voice/start", "/api/voice/stop", "/api/voice/discard"}


def test_cancelling_the_disconnect_confirmation_sends_nothing():
    for name in ("openDisconnect", "cancelDisconnect"):
        body = _fn(name)
        assert "api(" not in body and "fetch(" not in body and "Post(" not in body
    assert "$('btn-google-disconnect').onclick = openDisconnect;" in SCRIPT
    text = PAGE_HTML.split('id="google-confirm"', 1)[1].split("</div>\n    </div>", 1)[0]
    assert "will be deleted" in text and "revoke" in text and "<b>not</b> deleted" in text


def test_status_indicators_cover_ready_running_and_audit(page):
    assert {"st-engine", "st-audit"} <= page.ids
    for text in ("MAESTRO ready", "Running task", "Audit log intact",
                 "Audit log unavailable", "Audit log BROKEN"):
        assert text in SCRIPT


def test_progress_is_readable_while_a_run_holds_the_lock(ws):
    """The status heartbeat polls /api/progress; it must never wait on a run."""
    done = threading.Event()
    with ws.lock:
        t = threading.Thread(target=lambda: (ws.progress_json(), done.set()))
        t.start()
        assert done.wait(2.0), "progress_json blocked on the workspace lock"
    t.join()


def test_integrations_failure_is_a_safe_answer(monkeypatch):
    from maestro.google import auth

    def boom():
        raise RuntimeError("/Users/someone/.maestro/google/token.json: SECRET")

    monkeypatch.setattr(auth, "integration_status", boom)
    out = ui.integrations_json()
    assert out["google"]["state"] == "unavailable"
    text = json.dumps(out)
    assert "SECRET" not in text and "/Users" not in text and "RuntimeError" not in text
    assert set(out["google"]["services"]) == {"gmail", "drive", "calendar"}


def test_integrations_check_creates_no_google_folder():
    from maestro.google import auth

    ui.integrations_json()
    assert not auth.google_home().exists()



# --------------------------------------------------------------------------- #
# Google connect / disconnect from the Connections page (all mocked)
# --------------------------------------------------------------------------- #

ALL_SCOPES = [f"https://www.googleapis.com/auth/{x}" for x in
              ("gmail.readonly", "gmail.compose", "drive.readonly", "drive.file",
               "calendar.events")]


class _FakeCreds:
    def to_json(self):
        return json.dumps({"token": "ACCESS-SECRET", "refresh_token": "REFRESH-SECRET",
                           "client_secret": "CLIENT-SECRET", "scopes": ALL_SCOPES,
                           "expiry": "2099-01-01T00:00:00Z"})


@pytest.fixture
def google(monkeypatch):
    """A fake Google: connect() and the network are mocked; nothing real is reachable."""
    import socket

    from maestro.google import auth

    state = types.SimpleNamespace(connect_calls=[], urls=[], release=threading.Event(),
                                  fail=None, block=False, revoke_ok=False)
    state.release.set()

    def fake_connect(open_browser=True, timeout_seconds=None):
        state.connect_calls.append({"open_browser": open_browser, "timeout": timeout_seconds})
        state.release.wait(10)
        if state.fail:
            raise state.fail
        auth._save(_FakeCreds())          # the real owner-only token writer

    def fake_urlopen(req, timeout=None):
        state.urls.append(getattr(req, "full_url", str(req)))
        if not state.revoke_ok:
            raise OSError("network is off in tests")
        return types.SimpleNamespace(status=200)

    real_create = socket.create_connection

    def local_only(address, *a, **k):
        if address[0] not in ("127.0.0.1", "localhost"):
            raise AssertionError("no network in tests")
        return real_create(address, *a, **k)

    def no_browser(*a, **k):
        raise AssertionError("no browser may be opened in tests")

    def no_data(*a, **k):
        raise AssertionError("Gmail / Drive / Calendar must not be accessed")

    monkeypatch.setattr(auth, "connect", fake_connect)
    monkeypatch.setattr(auth, "credentials", no_data)
    monkeypatch.setattr(auth, "_libraries_installed", lambda: True)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(socket, "create_connection", local_only)
    monkeypatch.setattr("webbrowser.open", no_browser)
    monkeypatch.delenv("MAESTRO_GOOGLE_CLIENT_SECRET", raising=False)
    state.auth = auth
    return state


def _client_file(auth):
    auth.google_dir()
    auth.client_secret_path().write_text('{"installed": {"client_secret": "CLIENT-SECRET"}}')


@pytest.fixture
def http(ws):
    """The real handler on a random port; returns get(path) and post(path, headers)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ws))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(method, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     data=b"{}" if method == "POST" else None,
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
        try:
            with _real_urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(path, headers=None):
        status, body = call("GET", path, headers)
        return status, (body if path == "/" else json.loads(body))

    def post(path, headers=None):
        status, body = call("POST", path, headers)
        return status, json.loads(body)

    try:
        yield types.SimpleNamespace(get=get, post=post, ws=ws,
                                    token={"X-MAESTRO-Token": ws.request_token})
    finally:
        httpd.shutdown()
        httpd.server_close()


_real_urlopen = urllib.request.urlopen
CONNECT = "/api/integrations/google/connect"
DISCONNECT = "/api/integrations/google/disconnect"
FLOW = "/api/integrations/google/flow"


def _wait_flow(http, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, f = http.get(FLOW)
        if f["state"] != "connecting":
            return f
        time.sleep(0.05)
    raise AssertionError("sign-in flow did not finish")


def test_page_load_and_status_reads_never_start_sign_in(google, http):
    _client_file(google.auth)
    status, page = http.get("/")
    assert status == 200 and http.ws.request_token.encode() in page
    for _ in range(3):
        assert http.get("/api/integrations")[1]["google"]["state"] == "connect_required"
        assert http.get(FLOW)[1] == {"state": "idle", "message": ""}
    assert google.connect_calls == [] and google.urls == []


def test_connect_without_a_valid_request_token_is_refused(google, http):
    _client_file(google.auth)
    bad = [{}, {"X-MAESTRO-Token": "wrong"},
           {**http.token, "Origin": "https://evil.example"},
           {**http.token, "Host": "evil.example:8765"}]
    for headers in bad:
        status, out = http.post(CONNECT, headers)
        assert status == 403 and "error" in out, headers
    assert google.connect_calls == []
    assert http.get(FLOW)[1]["state"] == "idle"


def test_token_is_not_served_to_a_foreign_host(google, http):
    status, page = http.get("/", {"Host": "rebound.example:8765"})
    assert status == 200 and http.ws.request_token.encode() not in page


def test_explicit_connect_runs_one_background_sign_in_and_connects(google, http):
    _client_file(google.auth)
    google.release.clear()                                  # hold the fake sign-in open
    status, out = http.post(CONNECT, http.token)
    assert status == 202 and out["state"] == "connecting"   # returned at once
    assert http.get(FLOW)[1]["state"] == "connecting"
    status, out = http.post(CONNECT, http.token)            # a second click
    assert status == 409 and "already in progress" in out["error"]
    google.release.set()
    assert _wait_flow(http)["state"] == "connected"
    assert len(google.connect_calls) == 1
    assert google.connect_calls[0] == {"open_browser": True,
                                       "timeout": ui.GOOGLE_SIGNIN_TIMEOUT_S}
    assert http.get("/api/integrations")[1]["google"]["state"] == "connected"


@pytest.mark.parametrize("setup", ["nothing", "connected"])
def test_connect_is_refused_when_not_connectable(google, http, setup):
    if setup == "connected":
        _client_file(google.auth)
        google.auth._save(_FakeCreds())
    status, out = http.post(CONNECT, http.token)
    assert status == 409
    assert ("set up" in out["error"]) if setup == "nothing" else ("already" in out["error"])
    assert google.connect_calls == []


def test_reconnect_is_allowed(google, http):
    google.auth.google_dir()
    google.auth.token_path().write_text(json.dumps({"scopes": ALL_SCOPES[:2],
                                                    "refresh_token": "R"}))
    assert http.get("/api/integrations")[1]["google"]["state"] == "reconnect_required"
    assert http.post(CONNECT, http.token)[0] == 202
    assert _wait_flow(http)["state"] == "connected"


def test_failed_sign_in_is_reported_safely(google, http, tmp_path):
    _client_file(google.auth)
    google.fail = RuntimeError(f"{tmp_path}/client_secret.json CLIENT-SECRET code=4/abc")
    assert http.post(CONNECT, http.token)[0] == 202
    f = _wait_flow(http)
    text = json.dumps(f)
    assert f["state"] == "failed" and "try again" in f["message"]
    for leak in ("CLIENT-SECRET", "client_secret.json", str(tmp_path), "RuntimeError", "4/abc"):
        assert leak not in text
    assert http.get("/api/integrations")[1]["google"]["state"] == "connect_required"
    google.fail = None
    assert http.post(CONNECT, http.token)[0] == 202            # retry is allowed
    assert _wait_flow(http)["state"] == "connected"


def test_disconnect_needs_the_request_token(google, http):
    _client_file(google.auth)
    google.auth._save(_FakeCreds())
    for headers in ({}, {"X-MAESTRO-Token": "nope"}):
        assert http.post(DISCONNECT, headers)[0] == 403
    assert google.auth.token_path().exists() and google.urls == []


def test_disconnect_removes_only_the_token_even_if_revocation_fails(google, http, workspace):
    _client_file(google.auth)
    google.auth._save(_FakeCreds())
    keep = google.auth.google_home() / "notes.txt"
    keep.write_text("not MAESTRO's token")
    (workspace / "mail.eml").write_text("a draft")
    status, out = http.post(DISCONNECT, http.token)
    assert status == 200 and out["ok"] and out["removed"] and out["revoked"] is False
    assert "could not be reached" in out["message"]
    assert out["google"]["state"] == "connect_required"
    assert not google.auth.token_path().exists()
    assert google.auth.client_secret_path().exists() and keep.exists()
    assert (workspace / "mail.eml").exists()
    assert google.urls == ["https://oauth2.googleapis.com/revoke"]   # nothing else contacted
    for secret in ("ACCESS-SECRET", "REFRESH-SECRET", "CLIENT-SECRET"):
        assert secret not in json.dumps(out)


def test_disconnect_reports_successful_revocation(google, http):
    google.revoke_ok = True
    google.auth._save(_FakeCreds())
    status, out = http.post(DISCONNECT, http.token)
    assert status == 200 and out["revoked"] is True and "revoked at Google" in out["message"]


def test_disconnect_when_not_connected_or_while_signing_in(google, http):
    assert http.post(DISCONNECT, http.token)[0] == 409
    _client_file(google.auth)
    google.release.clear()
    assert http.post(CONNECT, http.token)[0] == 202
    status, out = http.post(DISCONNECT, http.token)
    assert status == 409 and "finish" in out["error"]
    google.release.set()
    _wait_flow(http)


def test_cli_google_commands_still_work(google, capsys):
    from maestro import cli

    assert cli.main(["google", "status"]) == 0
    assert "MISSING" in capsys.readouterr().out
    assert not google.auth.google_home().exists()          # status stays read-only
    _client_file(google.auth)
    assert cli.main(["google", "connect", "--no-browser"]) == 0
    assert google.connect_calls[-1]["open_browser"] is False
    assert "connected" in capsys.readouterr().out
    assert cli.main(["google", "disconnect"]) == 0
    assert "disconnected" in capsys.readouterr().out
    assert not google.auth.token_path().exists()
    assert cli.main(["google", "disconnect"]) == 0
    assert "was not connected" in capsys.readouterr().out



# --------------------------------------------------------------------------- #
# Voice page: push-to-talk (fake microphone and fake transcriber only)
# --------------------------------------------------------------------------- #

def test_voice_page_reads_status_with_get_and_never_starts_recording():
    assert re.findall(r"'(/api/[\w/]+)'", _fn("watchVoice")) == ["/api/voice/status"]
    for name in ("renderVoice", "watchVoice", "go", "boot"):
        body = _fn(name)
        assert "startVoice(" not in body and "voiceAction(" not in body
    assert "startVoice(" not in SCRIPT.replace("async function startVoice(", "")
    assert "$('btn-voice-start').onclick = startVoice;" in SCRIPT


def test_use_in_assistant_only_fills_the_instruction_box():
    body = _fn("useVoiceText")
    assert "$('instruction').value = text" in body and "#assistant" in body
    for forbidden in ("/api/plan", "/api/approve", "/api/answer", "plan(", "approve(",
                      "answer(", "/api/voice/start"):
        assert forbidden not in body, forbidden


def test_voice_controls_are_labelled_and_announced():
    voice = PAGE_HTML.split('data-section="voice"', 1)[1].split("</section>", 1)[0]
    for bid, label in (("btn-voice-start", "Start recording"), ("btn-voice-stop", "Stop recording"),
                       ("btn-voice-use", "Use this text in the Assistant"),
                       ("btn-voice-discard", "Discard this transcript")):
        assert f'id="{bid}" aria-label="{label}"' in voice
    assert 'id="voice-state" role="status" aria-live="polite"' in voice
    assert '<label for="voice-text">' in voice
    assert "$('voice-text').focus()" in _fn("renderVoice")
    assert "$('btn-voice-start').focus()" in _fn("discardVoice")
    assert "never uploaded" in voice and "permission belongs to the app" in voice


class _FakeMic:
    """Fake MicEars for the HTTP tests: counts opens/closes; never touches hardware."""

    def __init__(self, reg):
        self.reg = reg
        self.transcriber = self

    def frames(self):
        import numpy as np

        self.reg["opened"] += 1
        try:
            while True:
                time.sleep(0.002)
                yield np.where(np.arange(480) % 2, 0.05, -0.05).astype("float32")
        finally:
            self.reg["closed"] += 1

    def transcribe(self, audio):
        from maestro.voice import Heard

        return Heard("move the pdfs from inbox to archive", 0.88, "voice")


@pytest.fixture
def voice_http(http, monkeypatch):
    from maestro.voice import capture, ears

    def forbidden(*a, **k):
        raise AssertionError("real microphone or speech model used in a test")

    monkeypatch.setattr(ears.MicEars, "frames", forbidden)
    monkeypatch.setattr(ears.MicEars, "_sd", staticmethod(forbidden))
    monkeypatch.setattr(ears.WhisperTranscriber, "load", forbidden)
    monkeypatch.setattr(capture, "_missing_deps", lambda: [])
    monkeypatch.setattr(capture.VoiceCapture, "_describe_device", lambda self: "Fake Mic")
    reg = {"opened": 0, "closed": 0}
    http.ws.voice = capture.VoiceCapture(ears_factory=lambda: _FakeMic(reg))
    http.mic = reg
    return http


def _until_audio(cap, seconds=0.45, timeout=10.0):
    """Wait for `seconds` of recorded AUDIO (frames), not wall-clock time: a fixed sleep can
    leave less than the 0.3 s minimum on a slow runner, which is rejected as "No audio".
    """
    t0 = time.time()
    while cap.elapsed < seconds and time.time() - t0 < timeout:
        time.sleep(0.005)
    assert cap.elapsed >= seconds, f"only {cap.elapsed:.2f} s of audio was recorded"


def _voice_settle(http, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = http.get("/api/voice/status")[1]
        if st["state"] not in ("starting", "listening", "transcribing"):
            return st
        time.sleep(0.02)
    raise AssertionError("voice capture did not settle")


def test_page_load_and_voice_status_never_open_the_microphone(voice_http):
    assert voice_http.get("/")[0] == 200
    for _ in range(3):
        st = voice_http.get("/api/voice/status")[1]
        assert st["state"] == "idle" and st["device"] == "Fake Mic"
    assert voice_http.mic["opened"] == 0


@pytest.mark.parametrize("path", ["/api/voice/start", "/api/voice/stop", "/api/voice/discard"])
def test_voice_controls_need_the_request_token(voice_http, path):
    bad = [{}, {"X-MAESTRO-Token": "wrong"},
           {**voice_http.token, "Origin": "https://evil.example"},
           {**voice_http.token, "Host": "evil.example:8765"}]
    for headers in bad:
        status, out = voice_http.post(path, headers)
        assert status == 403 and "error" in out, (path, headers)
    assert voice_http.mic["opened"] == 0
    assert voice_http.get("/api/voice/status")[1]["state"] == "idle"


def test_voice_flow_over_http_returns_text_and_runs_nothing(voice_http, aliased):
    episodes_before = len(voice_http.ws.history())
    status, out = voice_http.post("/api/voice/start", voice_http.token)
    assert status == 202 and out["state"] in ("starting", "listening")
    assert voice_http.post("/api/voice/start", voice_http.token)[0] == 409
    _until_audio(voice_http.ws.voice)            # enough recorded audio, however slow the runner
    assert voice_http.post("/api/voice/stop", voice_http.token)[0] == 202
    st = _voice_settle(voice_http)
    assert st["state"] == "transcript_ready"
    assert st["transcript"] == "move the pdfs from inbox to archive" and st["confidence"] == 0.88
    assert voice_http.mic == {"opened": 1, "closed": 1}
    # Text only: no plan was made, nothing previewed, nothing moved, no episode recorded.
    assert voice_http.ws.state()["previewed"] is None and voice_http.ws.state()["pending"] is None
    assert len(voice_http.ws.history()) == episodes_before
    assert (aliased / "inbox" / "a.pdf").exists()
    status, out = voice_http.post("/api/voice/discard", voice_http.token)
    assert status == 200 and out["state"] == "idle" and out["transcript"] == ""
    assert voice_http.post("/api/voice/stop", voice_http.token)[0] == 409


def test_closing_the_workspace_releases_a_recording_microphone(voice_http):
    voice_http.post("/api/voice/start", voice_http.token)
    t0 = time.time()
    while voice_http.mic["opened"] == 0 and time.time() - t0 < 10:
        time.sleep(0.005)                         # the microphone really is open
    assert voice_http.mic["opened"] == 1
    voice_http.ws.voice.shutdown()
    assert voice_http.mic["closed"] == voice_http.mic["opened"] == 1


def test_voice_stopping_state_keeps_start_disabled_and_is_labelled():
    from maestro.voice.capture import BUSY

    assert "stopping" in BUSY
    assert "stopping:         ['Stopping microphone…', 'run']" in SCRIPT
    busy = re.search(r"const V_BUSY = \[(.*?)\];", SCRIPT).group(1)
    assert sorted(x.strip(" '") for x in busy.split(",")) == sorted(BUSY)
    assert "V_BUSY.includes(v.state)" in _fn("renderVoice")
    assert "watchVoice()" in _fn("discardVoice")
