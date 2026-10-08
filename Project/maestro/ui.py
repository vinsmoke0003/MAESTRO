"""A local web workspace over the existing pipeline. `maestro ui` serves it.

One page, one job: show the whole safety story of a single instruction in one
place — instruction → clarifying question (if any) → action preview with the
risk tier and the rules that produced it → approve / deny (typed token for
R3) → live step progress → outputs → history with verified undo.

Deliberately stdlib only (`http.server`), bound to 127.0.0.1, no framework, no
build step: this is a demonstration surface, not a product front end. Every
endpoint calls the same `MaestroPipeline` methods the CLI calls, so nothing
shown here is a second implementation of the pipeline.

Approval is two-phase and fingerprinted. `/api/plan` runs the pipeline in
preview mode (nothing executes; see tests/test_preview.py). `/api/approve`
re-runs the same instruction with the same clarification answers and hands the
consent gate a one-shot grant keyed on the previewed plan's fingerprint. If
the re-planned plan differs from what the user looked at, the grant does not
apply and the run is denied — the user approves the plan they saw, not the
instruction.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import maestro.executor  # noqa: F401  (register executors)
from maestro.ir import Plan
from maestro.orchestrator import Orchestrator, StepReport, render_preview
from maestro.pipeline import MaestroPipeline, Turn
from maestro.safety import ConsentGate
from maestro.safety.consent import Approval, ConsentRequest, token_matches

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


# --------------------------------------------------------------------------- #
# workspace state
# --------------------------------------------------------------------------- #


class Workspace:
    """Everything the page can see. One instruction in flight at a time."""

    def __init__(self, pipeline: MaestroPipeline | None = None):
        """Set up the web workspace around one pipeline, routing all consent through the page's
        Approve button and tracking step progress.
        """
        self.lock = threading.Lock()
        self.gate = ConsentGate(ask=self._ask)
        self.pipe = pipeline or MaestroPipeline(gate=self.gate)
        if pipeline is not None:
            # a caller-supplied pipeline still has to route consent through us
            self.pipe.gate = self.gate
            self.pipe.orchestrator.gate = self.gate
        self.pipe.orchestrator.on_step = self._on_step

        self.pending: Turn | None = None      # a clarification awaiting an answer
        self.previewed: Turn | None = None    # the plan the user is looking at
        self._grant: tuple[str, str] | None = None   # (fingerprint, typed token)
        self.progress: list[dict] = []
        self.job: dict | None = None          # running execution, if any

        # Google sign-in from the Connections page, and the per-server token that
        # every request changing the Google connection must carry (see make_handler).
        self.google_flow = GoogleFlow()
        self.request_token = secrets.token_urlsafe(32)
        # Push-to-talk for the Voice page. Produces text only; it never plans or runs.
        from maestro.voice.capture import VoiceCapture

        self.voice = VoiceCapture()

    # ---- consent ---------------------------------------------------------

    def _ask(self, req: ConsentRequest) -> Approval:
        """The consent callback: approve only if the user clicked Approve for exactly the plan they
        previewed (same fingerprint), and for a high-risk plan typed the right confirmation
        word.
        """
        grant, self._grant = self._grant, None
        if grant is None:
            return Approval(False, "denied", note="no approval was given in the UI")
        fingerprint, typed = grant
        if req.plan.fingerprint() != fingerprint:
            return Approval(False, "denied",
                            note="the plan changed since it was previewed — preview again")
        if req.gate == "typed_confirm":
            token = req.token or "CONFIRM"
            if not token_matches(typed, token):
                return Approval(False, "denied", note="typed confirmation did not match")
            return Approval(True, "typed")
        return Approval(True, "click")

    def _on_step(self, step: StepReport) -> None:
        """Record each finished step so the page can show live progress."""
        self.progress.append({
            "action_id": step.action_id, "verb": step.verb, "ok": step.ok,
            "detail": step.detail, "ms": round(step.ms, 1),
            "files_touched": step.files_touched,
            "checks": [{"ok": c.ok, "detail": getattr(c, "detail", "")}
                       for c in step.checks],
        })

    # ---- turns -----------------------------------------------------------

    def plan(self, instruction: str) -> dict:
        """Plan an instruction and return the preview (or a clarifying question); nothing is
        executed.
        """
        with self.lock:
            turn = self.pipe.handle(instruction, preview_only=True)
            return self._settle(turn)

    def answer(self, answer: str) -> dict:
        """Answer the pending clarifying question and return the new preview."""
        with self.lock:
            if self.pending is None:
                return {"error": "there is no pending question"}
            turn = self.pipe.resume(self.pending, answer, preview_only=True)
            return self._settle(turn)

    def _settle(self, turn: Turn) -> dict:
        """Remember whether the turn is waiting for an answer or showing a preview, and return it
        as JSON.
        """
        self.pending = turn if turn.status == "clarified" else None
        self.previewed = turn if turn.status == "previewed" else None
        return turn_json(turn)

    def deny(self) -> dict:
        """Throw away the previewed plan or pending question; nothing changes."""
        with self.lock:
            had = self.previewed is not None
            self.previewed = None
            self.pending = None
            return {"ok": True, "message": "denied — nothing was changed" if had
                    else "nothing was pending"}

    def approve(self, typed: str = "") -> dict:
        """Start executing the previewed plan in the background. Refuses if nothing is previewed or
        a run is already going.
        """
        with self.lock:
            if self.previewed is None:
                return {"error": "nothing is previewed — preview a plan first"}
            if self.job and not self.job.get("done"):
                return {"error": "a run is already in progress"}
            prev = self.previewed
            self.previewed = None
            self.progress = []
            self.job = {"done": False, "started": time.time(), "instruction": prev.instruction,
                        "total": len(prev.plan.actions) if prev.plan else 0}
            self._grant = (prev.plan.fingerprint(), typed)
            threading.Thread(target=self._execute, args=(prev,), daemon=True).start()
            return {"started": True, "total": self.job["total"]}

    def _execute(self, prev: Turn) -> None:
        """Background thread: re-run the instruction bound to the previewed plan's fingerprint, so
        only that exact plan can execute, and store the result.
        """
        # Holds the workspace lock for the whole run: the stores' SQLite
        # connections are shared across threads on the strength of this lock.
        # `/api/progress` reads only `self.progress`, so polling stays live.
        with self.lock:
            try:
                # Bound to the previewed plan for EVERY tier: the consent
                # callback below is a second check, but R0/R1 plans never
                # consult it, so the pipeline compares fingerprints before
                # the orchestrator runs.
                turn = self.pipe.handle(prev.instruction,
                                        slot_overrides=prev.slot_overrides,
                                        intent_override=prev.intent_override,
                                        expected_fingerprint=prev.plan.fingerprint())
                self.job["result"] = turn_json(turn)
            except Exception as e:  # surfaced to the page, never swallowed
                self.job["result"] = {"status": "failed",
                                      "message": f"{type(e).__name__}: {e}"}
            finally:
                self._grant = None
                self.job["done"] = True

    def progress_json(self) -> dict:
        """The running job's status, finished steps and (once done) the result, for the page to
        poll.
        """
        job = self.job or {"done": True}
        return {**{k: v for k, v in job.items() if k != "result"},
                "steps": list(self.progress),
                "result": job.get("result") if job.get("done") else None}

    # ---- history / undo ------------------------------------------------------

    def history(self, limit: int = 25) -> list[dict]:
        """Recent episodes for the History panel."""
        with self.lock:
            return self._history(limit)

    def _history(self, limit: int) -> list[dict]:
        """Recent episodes as rows, each marked with whether it can still be undone."""
        rows = []
        for r in self.pipe.episodes.recent(limit):
            undoable = self.pipe.episodes.is_undoable(r)
            rows.append({
                "episode_id": r.get("episode_id"),
                "ts": r.get("ts"),
                "instruction": r.get("instruction"),
                "intent": r.get("intent"),
                "status": r.get("status"),
                "risk": r.get("plan_risk") or r.get("risk"),
                "gate": r.get("gate"),
                "strategy": r.get("strategy"),
                "detail": (r.get("detail") or "")[:200],
                "undoable": undoable,
            })
        return rows

    def undo(self, episode_id: str | None) -> dict:
        """Undo the given (or latest undoable) run from its stored plan and results, and record
        whether the undo fully succeeded.
        """
        with self.lock:
            store = self.pipe.episodes
            row = store.last_undoable(episode_id)
            if row is None:
                return {"ok": False, "message": "nothing to undo — no completed run with a "
                                                "reversible step is recorded"}
            plan = Plan.model_validate(json.loads(row["plan_json"]))
            variables = json.loads(row["variables_json"] or "{}")
            executed = [a.action_id for a in plan.actions]
            orch = Orchestrator(policy=self.pipe.orchestrator.policy,
                                audit=self.pipe.orchestrator.audit)
            result = orch.undo_run(plan, variables=variables, executed=executed)
            if result.ok:
                store.mark_undone(row["episode_id"], result.render()[:500])
            else:
                # Failed or partial: say so in the history and keep the run
                # eligible for another attempt. Never file it as "undone".
                store.mark_undo_failed(row["episode_id"], result.render()[:500])
            return {
                "ok": result.ok,
                "episode_id": row["episode_id"],
                "instruction": plan.instruction,
                "reversed": result.reversed,
                "verified_files": result.verified_files,
                "mismatched_files": result.mismatched_files,
                "collisions": result.collisions,
                "skipped": [list(s) for s in result.skipped],
                "failed": [list(f) for f in result.failed],
                "render": result.render(),
            }

    def audit(self) -> dict:
        """Audit log status for the page."""
        with self.lock:
            return self._audit()

    def _audit(self) -> dict:
        """Whether the audit log's hash chain is intact, the number of events, and the last few."""
        log = self.pipe.orchestrator.audit
        if log is None:
            return {"available": False}
        rows = log.rows()
        tail = rows[-8:]
        return {"available": True, "intact": bool(log.verify()), "events": len(rows),
                "tail": [{"event": r.event, "verb": getattr(r, "verb", ""),
                          "detail": (getattr(r, "detail", "") or "")[:90]} for r in tail]}

    def state(self) -> dict:
        """Current state for the page: pipeline description, pending question, preview, and whether
        a run is going.
        """
        with self.lock:
            return self._state()

    def _state(self) -> dict:
        """Build the state dict (the caller holds the lock)."""
        return {"pipeline": self.pipe.describe(),
                "pending": turn_json(self.pending) if self.pending else None,
                "previewed": turn_json(self.previewed) if self.previewed else None,
                "running": bool(self.job and not self.job.get("done"))}

    def close(self) -> None:
        """Release the microphone if a recording is open, then close the pipeline's databases."""
        try:
            self.voice.shutdown()
        finally:
            self.pipe.close()


# --------------------------------------------------------------------------- #
# Google connect / disconnect from the Connections page
# --------------------------------------------------------------------------- #

CONNECTABLE = ("connect_required", "reconnect_required")
GOOGLE_SIGNIN_TIMEOUT_S = 300

_FLOW_MESSAGES = {
    "idle": "",
    "connecting": "Waiting for Google sign-in… Finish it in the browser window Google opened.",
    "connected": "Connected to Google.",
    "failed": "Google sign-in did not complete. Nothing was changed. You can try again.",
}


class GoogleFlow:
    """At most one Google sign-in at a time, run in the background.

    The sign-in itself is `auth.connect()`, the same code `maestro google connect`
    runs: it opens Google's own page in the browser and stores the token with the
    existing owner-only writer. This class only tracks a safe state for the page:
    idle -> connecting -> connected | failed. The URL, the code Google returns,
    tokens and error details are never kept here, so they can never be served.
    """

    def __init__(self, timeout_s: int = GOOGLE_SIGNIN_TIMEOUT_S):
        """Start idle; `timeout_s` ends a sign-in the user walked away from."""
        self._lock = threading.Lock()
        self.state = "idle"
        self.timeout_s = timeout_s

    def snapshot(self) -> dict:
        """The current state and its fixed, user-facing message."""
        with self._lock:
            return {"state": self.state, "message": _FLOW_MESSAGES[self.state]}

    def start(self) -> tuple[int, dict]:
        """Start one sign-in if allowed. Returns (HTTP status, answer) immediately."""
        from maestro.google import auth

        with self._lock:
            if self.state == "connecting":
                return 409, {"error": "A Google sign-in is already in progress."}
            st = _google_status()
            if st["state"] not in CONNECTABLE:
                return 409, {"error": _NOT_CONNECTABLE.get(
                    st["state"], "Google cannot be connected right now.")}
            self.state = "connecting"
        threading.Thread(target=self._run, args=(auth,), daemon=True,
                         name="google-signin").start()
        return 202, self.snapshot()

    def _run(self, auth) -> None:
        """Background thread: run the existing sign-in and record only success or failure."""
        try:
            auth.connect(open_browser=True, timeout_seconds=self.timeout_s)
            ok = True
        except BaseException:  # noqa: BLE001 - every failure becomes the same safe message
            ok = False
        with self._lock:
            self.state = "connected" if ok else "failed"

    @property
    def busy(self) -> bool:
        """True while a sign-in is running."""
        with self._lock:
            return self.state == "connecting"


_NOT_CONNECTABLE = {
    "setup_required": "Google is not set up yet: add the OAuth client file first "
                      "(see docs/GOOGLE-SETUP.md).",
    "connected": "Google is already connected.",
    "unavailable": "Google status could not be read, so sign-in was not started.",
}


def _google_status() -> dict:
    """The read-only Google status, or the safe 'unavailable' answer."""
    return integrations_json()["google"]


def google_disconnect(flow: GoogleFlow) -> tuple[int, dict]:
    """Delete MAESTRO's Google token and try to revoke it at Google. Mail, Drive files and
    calendar events are not touched.
    """
    if flow.busy:
        return 409, {"error": "Wait for the Google sign-in to finish first."}
    st = _google_status()
    if not (st.get("token_present") or st["state"] in ("connected", "reconnect_required")):
        return 409, {"error": "MAESTRO is not connected to Google."}
    try:
        from maestro.google import auth

        out = auth.disconnect_detailed()
    except Exception:  # noqa: BLE001 - no detail reaches the page
        return 500, {"ok": False, "removed": False, "revoked": None,
                     "message": "Disconnect did not complete. The sign-in may still be "
                                "stored; try again.",
                     "google": _google_status()}
    removed, revoked = bool(out.get("removed")), out.get("revoked")
    if removed and revoked:
        msg = "Disconnected. The local sign-in was deleted and access was revoked at Google."
    elif removed:
        msg = ("Disconnected. The local sign-in was deleted, but Google could not be reached "
               "to revoke access; you can remove MAESTRO at myaccount.google.com/permissions.")
    else:
        msg = "The local sign-in could not be deleted. MAESTRO may still be connected."
    return 200, {"ok": removed, "removed": removed, "revoked": revoked, "message": msg,
                 "google": _google_status()}


# --------------------------------------------------------------------------- #
# serialisation
# --------------------------------------------------------------------------- #


def turn_json(turn: Turn) -> dict:
    """A turn as JSON for the page: status, clarification, and every action with its risk, reasons,
    taint and dry-run summary.
    """
    d = turn.as_dict()
    d["rounds"] = turn.rounds
    d["clarification"] = None
    if turn.clarification and turn.clarification.needed:
        d["clarification"] = {"question": turn.message,
                              "options": list(turn.clarification.options or [])}
    rep = turn.report
    d["actions"] = []
    if turn.plan:
        verdicts = {a.action_id: a for a in rep.verdict.actions} if rep and rep.verdict else {}
        for a in turn.plan.actions:
            v = verdicts.get(a.action_id)
            d["actions"].append({
                "action_id": a.action_id,
                "verb": a.verb,
                "args": _compact_args(a.args),
                "produces": a.produces,
                "depends_on": list(a.depends_on),
                "undo": a.undo.verb if a.undo else None,
                "risk": str(v.risk) if v else None,
                "reasons": list(v.reasons) if v else [],
                "rules_fired": list(v.rules_fired) if v else [],
                "trust": str(v.trust) if v else None,
            })
    d["manifests"] = [{
        "summary": m.summary, "files_touched": m.files_touched,
        "bytes_affected": m.bytes_affected,
        "creates": m.creates[:8], "modifies": m.modifies[:8], "removes": m.removes[:8],
        "collisions": m.collisions[:8], "unknowns": m.unknowns, "external": m.external,
    } for m in (rep.manifests if rep else [])]
    d["critic"] = rep.critic.as_dicts() if rep and rep.critic else []
    d["steps"] = [{
        "action_id": s.action_id, "verb": s.verb, "ok": s.ok, "detail": s.detail,
        "ms": round(s.ms, 1), "files_touched": s.files_touched,
    } for s in (rep.steps if rep else [])]
    d["token"] = None
    if rep and rep.verdict and turn.plan and rep.gate == "typed_confirm":
        d["token"] = ConsentRequest(turn.plan, rep.verdict, rep.manifests).token
    d["preview_text"] = (render_preview(turn.plan, rep.verdict, rep.manifests)
                         if turn.plan and rep and rep.verdict else turn.preview)
    d["approval"] = asdict(rep.approval) if rep and rep.approval else None
    return d


def _compact_args(args: dict) -> dict:
    """Shorten long lists and strings in action arguments so the preview stays readable."""
    out = {}
    for k, v in args.items():
        if isinstance(v, list) and len(v) > 4:
            out[k] = v[:3] + [f"… +{len(v) - 3} more"]
        elif isinstance(v, str) and len(v) > 100:
            out[k] = v[:97] + "…"
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

PAGE = (Path(__file__).parent / "ui.html")
TOKEN_PLACEHOLDER = b"__MAESTRO_REQUEST_TOKEN__"
TOKEN_HEADER = "X-MAESTRO-Token"
# State-changing requests that need the page's token (see Handler._authorized).
PROTECTED_POSTS = ("/api/integrations/google/connect", "/api/integrations/google/disconnect",
                   "/api/voice/start", "/api/voice/stop", "/api/voice/discard")
LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _host_only(value: str) -> str:
    """'localhost:8765' -> 'localhost'; '[::1]:80' -> '::1'; lower-cased."""
    v = (value or "").strip().lower()
    if v.startswith("["):
        return v[1:].split("]", 1)[0]
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def integrations_json() -> dict:
    """Read-only status of the external integrations, for the Connections page.

    Local files only: no folder is created, no token refreshed, no network call
    made. Any failure becomes a generic 'unavailable' answer, so the page never
    sees an exception, a path or a credential.
    """
    try:
        from maestro.google import auth

        google = auth.integration_status()
    except Exception:
        google = {"state": "unavailable",
                  "message": "Google status could not be read on this machine.",
                  "services": {name: {"state": "unavailable", "permission": False, "scopes": []}
                               for name in ("gmail", "drive", "calendar")}}
    return {"google": google}


def make_handler(ws: Workspace):
    """Build the HTTP request handler class bound to this workspace."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "maestro-ui/1.0"

        def log_message(self, fmt, *args):  # quiet by default
            """Silence the default per-request logging."""
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            """Send a response with the given status, body and content type (never cached)."""
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            """Send an object as a JSON response."""
            self._send(status, json.dumps(obj, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            """Read the request body as JSON ({} if empty or invalid)."""
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return {}

        def _local_host(self) -> bool:
            """True if the request was addressed to this machine by name or loopback address.
            Refusing other Host names defeats DNS-rebinding pages that resolve to 127.0.0.1.
            """
            return _host_only(self.headers.get("Host", "")) in LOCAL_HOSTS

        def _authorized(self) -> bool:
            """A Google-changing request must come from the MAESTRO page itself: a local Host,
            no foreign Origin, and this server's secret token in the custom header.
            """
            if not self._local_host():
                return False
            origin = self.headers.get("Origin")
            if origin and _host_only(urlparse(origin).netloc) not in LOCAL_HOSTS:
                return False
            sent = self.headers.get(TOKEN_HEADER, "")
            return hmac.compare_digest(sent.encode(), ws.request_token.encode())

        def do_GET(self):
            """Serve the page and the read-only endpoints: state, history, progress, audit and
            integrations.
            """
            path = urlparse(self.path).path
            if path == "/":
                # The request token goes only into the page served to a local Host name.
                token = ws.request_token.encode() if self._local_host() else b""
                page = PAGE.read_bytes().replace(TOKEN_PLACEHOLDER, token)
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(ws.state())
            elif path == "/api/history":
                self._json(ws.history())
            elif path == "/api/progress":
                self._json(ws.progress_json())
            elif path == "/api/integrations":
                self._json(integrations_json())
            elif path == "/api/integrations/google/flow":
                self._json(ws.google_flow.snapshot())
            elif path == "/api/voice/status":
                self._json(ws.voice.status())
            elif path == "/api/audit":
                self._json(ws.audit())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            """Handle the actions: plan, answer, approve, deny and undo. Errors are returned as
            JSON, never crash the server.
            """
            path = urlparse(self.path).path
            if path in PROTECTED_POSTS:
                return self._protected_post(path)
            body = self._body()
            try:
                if path == "/api/plan":
                    text = (body.get("instruction") or "").strip()
                    if not text:
                        return self._json({"error": "instruction is empty"}, 400)
                    return self._json(ws.plan(text))
                if path == "/api/answer":
                    return self._json(ws.answer((body.get("answer") or "").strip()))
                if path == "/api/approve":
                    return self._json(ws.approve((body.get("typed") or "").strip()))
                if path == "/api/deny":
                    return self._json(ws.deny())
                if path == "/api/undo":
                    return self._json(ws.undo(body.get("episode_id") or None))
                return self._json({"error": "not found"}, 404)
            except Exception as e:  # the page shows the error; the server survives
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def _protected_post(self, path: str) -> None:
            """Google connect/disconnect and the microphone controls: only for an authorized
            request from the MAESTRO page, so a foreign web page can neither sign in, sign out,
            nor switch the microphone on.
            """
            self._body()   # drain it; these endpoints take no input
            if not self._authorized():
                return self._json({"error": "This request was not sent by the MAESTRO page."},
                                  403)
            actions = {
                "/api/integrations/google/connect": ws.google_flow.start,
                "/api/integrations/google/disconnect": lambda: google_disconnect(ws.google_flow),
                "/api/voice/start": ws.voice.start,
                "/api/voice/stop": ws.voice.stop,
                "/api/voice/discard": ws.voice.discard,
            }
            try:
                status, out = actions[path]()
            except Exception:  # noqa: BLE001 - never echo internals for these endpoints
                status, out = 500, {"error": "The request could not be completed."}
            return self._json(out, status)

    return Handler


class LocalServer:
    """One Workspace behind the existing handler, on 127.0.0.1 only.

    Shared by `maestro ui` (served in the foreground) and `maestro desktop`
    (served on a background thread under a native window), so both run the same
    pipeline, consent gate, request-token checks, Google and voice code.
    `close()` is safe to call more than once: it stops the server if it runs in
    the background, closes the listening socket, then closes the Workspace
    (which releases the microphone and the pipeline's databases) exactly once.
    """

    def __init__(self, port: int = 0, *, pipeline: MaestroPipeline | None = None):
        """Create the Workspace and bind to 127.0.0.1:`port` (0 = any free port)."""
        self.ws = Workspace(pipeline)
        try:
            self.httpd = ThreadingHTTPServer((HOST, port), make_handler(self.ws))
        except BaseException:
            self.ws.close()
            raise
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    @property
    def url(self) -> str:
        """The page's address, e.g. http://127.0.0.1:54321/."""
        return f"http://{HOST}:{self.httpd.server_address[1]}/"

    def start(self) -> None:
        """Serve on a background thread (desktop mode)."""
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="maestro-http",
                                        daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        """Serve on the calling thread until Ctrl+C (`maestro ui`)."""
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            pass

    def close(self) -> None:
        """Stop serving, close the socket, close the Workspace. Runs its work only once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            if self._thread is not None and self._thread.is_alive():
                self.httpd.shutdown()          # only valid while serve_forever runs elsewhere
                self._thread.join(5)
            self.httpd.server_close()
        finally:
            self.ws.close()


def serve(port: int = DEFAULT_PORT, *, open_browser: bool = True,
          pipeline: MaestroPipeline | None = None) -> int:
    """Start the local web workspace on 127.0.0.1 (this machine only), optionally open the browser,
    and run until Ctrl+C.
    """
    srv = LocalServer(port, pipeline=pipeline)
    url = srv.url
    print(f"MAESTRO workspace at {url}   (Ctrl+C to stop)")
    print(f"[{srv.ws.pipe.describe()}]")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    finally:
        srv.close()
    return 0


__all__ = ["Workspace", "LocalServer", "serve", "turn_json", "DEFAULT_PORT"]
