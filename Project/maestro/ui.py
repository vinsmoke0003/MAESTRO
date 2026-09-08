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

import json
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

    # ---- consent ---------------------------------------------------------

    def _ask(self, req: ConsentRequest) -> Approval:
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
        self.progress.append({
            "action_id": step.action_id, "verb": step.verb, "ok": step.ok,
            "detail": step.detail, "ms": round(step.ms, 1),
            "files_touched": step.files_touched,
            "checks": [{"ok": c.ok, "detail": getattr(c, "detail", "")}
                       for c in step.checks],
        })

    # ---- turns -----------------------------------------------------------

    def plan(self, instruction: str) -> dict:
        with self.lock:
            turn = self.pipe.handle(instruction, preview_only=True)
            return self._settle(turn)

    def answer(self, answer: str) -> dict:
        with self.lock:
            if self.pending is None:
                return {"error": "there is no pending question"}
            turn = self.pipe.resume(self.pending, answer, preview_only=True)
            return self._settle(turn)

    def _settle(self, turn: Turn) -> dict:
        self.pending = turn if turn.status == "clarified" else None
        self.previewed = turn if turn.status == "previewed" else None
        return turn_json(turn)

    def deny(self) -> dict:
        with self.lock:
            had = self.previewed is not None
            self.previewed = None
            self.pending = None
            return {"ok": True, "message": "denied — nothing was changed" if had
                    else "nothing was pending"}

    def approve(self, typed: str = "") -> dict:
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
        job = self.job or {"done": True}
        return {**{k: v for k, v in job.items() if k != "result"},
                "steps": list(self.progress),
                "result": job.get("result") if job.get("done") else None}

    # ---- history / undo ------------------------------------------------------

    def history(self, limit: int = 25) -> list[dict]:
        with self.lock:
            return self._history(limit)

    def _history(self, limit: int) -> list[dict]:
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
        with self.lock:
            return self._audit()

    def _audit(self) -> dict:
        log = self.pipe.orchestrator.audit
        if log is None:
            return {"available": False}
        rows = log.rows()
        tail = rows[-8:]
        return {"available": True, "intact": bool(log.verify()), "events": len(rows),
                "tail": [{"event": r.event, "verb": getattr(r, "verb", ""),
                          "detail": (getattr(r, "detail", "") or "")[:90]} for r in tail]}

    def state(self) -> dict:
        with self.lock:
            return self._state()

    def _state(self) -> dict:
        return {"pipeline": self.pipe.describe(),
                "pending": turn_json(self.pending) if self.pending else None,
                "previewed": turn_json(self.previewed) if self.previewed else None,
                "running": bool(self.job and not self.job.get("done"))}

    def close(self) -> None:
        self.pipe.close()


# --------------------------------------------------------------------------- #
# serialisation
# --------------------------------------------------------------------------- #


def turn_json(turn: Turn) -> dict:
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


def make_handler(ws: Workspace):
    class Handler(BaseHTTPRequestHandler):
        server_version = "maestro-ui/1.0"

        def log_message(self, fmt, *args):  # quiet by default
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(status, json.dumps(obj, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(ws.state())
            elif path == "/api/history":
                self._json(ws.history())
            elif path == "/api/progress":
                self._json(ws.progress_json())
            elif path == "/api/audit":
                self._json(ws.audit())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
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

    return Handler


def serve(port: int = DEFAULT_PORT, *, open_browser: bool = True,
          pipeline: MaestroPipeline | None = None) -> int:
    ws = Workspace(pipeline)
    httpd = ThreadingHTTPServer((HOST, port), make_handler(ws))
    url = f"http://{HOST}:{httpd.server_address[1]}/"
    print(f"MAESTRO workspace at {url}   (Ctrl+C to stop)")
    print(f"[{ws.pipe.describe()}]")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        ws.close()
    return 0


__all__ = ["Workspace", "serve", "turn_json", "DEFAULT_PORT"]
