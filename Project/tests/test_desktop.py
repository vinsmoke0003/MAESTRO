"""`maestro desktop`: the native window over the SAME local server `maestro ui` uses.

No real window is opened: a fake `webview` module stands in for pywebview and
records what it was asked to do. While its `start()` "shows the window", the
tests talk to the real local server over loopback, then let the window close
and check that everything was shut down exactly once. No browser, microphone,
Google sign-in, model download or outside network is used.
"""

from __future__ import annotations

import socket
import sys
import types
import urllib.request

import pytest

from maestro import cli, desktop, ui


class _Events:
    def __init__(self):
        self.loaded = _Hook()


class _Hook:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class FakeWebview:
    """Records create_window/start; `during_start(url)` runs while the window is 'open'."""

    def __init__(self, during_start=None, fail_on=None):
        self.settings = {"OPEN_EXTERNAL_LINKS_IN_BROWSER": False, "ALLOW_DOWNLOADS": False}
        self.windows = []
        self.started = []
        self.during_start = during_start
        self.fail_on = fail_on
        self.seen = {}

    def create_window(self, title, url, **kw):
        if self.fail_on == "create_window":
            raise RuntimeError("no display /tmp/.X11-unix")
        win = types.SimpleNamespace(title=title, url=url, kw=kw, events=_Events(),
                                    current=url, loads=[])
        win.get_current_url = lambda: win.current
        win.load_url = lambda u: win.loads.append(u)
        self.windows.append(win)
        return win

    def start(self, **kw):
        self.started.append(kw)
        if self.fail_on == "start":
            raise RuntimeError("GUI backend failed: QtWebEngine missing")
        if self.during_start:
            self.during_start(self.windows[-1].url, self)


@pytest.fixture
def no_outside(monkeypatch):
    """Loopback only; no browser, no mic, no Google sign-in."""
    real = socket.create_connection

    def local_only(addr, *a, **k):
        if addr[0] not in ("127.0.0.1", "localhost"):
            raise AssertionError("outside network used in a desktop test")
        return real(addr, *a, **k)

    def forbidden(*a, **k):
        raise AssertionError("browser / microphone / Google used in a desktop test")

    from maestro.google import auth
    from maestro.voice import ears

    monkeypatch.setattr(socket, "create_connection", local_only)
    monkeypatch.setattr("webbrowser.open", forbidden)
    monkeypatch.setattr(auth, "connect", forbidden)
    monkeypatch.setattr(ears.MicEars, "frames", forbidden)
    monkeypatch.setattr(ears.WhisperTranscriber, "load", forbidden)


@pytest.fixture
def track(monkeypatch):
    """Count Workspace.close() calls and capture what the server was built from."""
    rec = types.SimpleNamespace(closes=0, handlers=[], servers=[])
    real_close = ui.Workspace.close
    real_handler = ui.make_handler
    real_server = ui.ThreadingHTTPServer

    def close(self):
        rec.closes += 1
        return real_close(self)

    def make_handler(ws):
        rec.handlers.append(ws)
        return real_handler(ws)

    class Server(real_server):
        def __init__(self, address, handler):
            rec.servers.append(self)
            rec.address = address
            super().__init__(address, handler)

    monkeypatch.setattr(ui.Workspace, "close", close)
    monkeypatch.setattr(ui, "make_handler", make_handler)
    monkeypatch.setattr(ui, "ThreadingHTTPServer", Server)
    return rec


def _install(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "webview", fake)


def _get(url, path=""):
    with urllib.request.urlopen(url + path, timeout=5) as r:
        return r.status, r.read()


def _refused(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return False
    except OSError:
        return True


# --------------------------------------------------------------------------- #


def test_desktop_command_is_registered():
    args = cli.build_parser().parse_args(["desktop"])
    assert args.func is cli.cmd_desktop and args.port == 0 and args.debug is False
    ui_args = cli.build_parser().parse_args(["ui"])           # `maestro ui` unchanged
    assert ui_args.func is cli.cmd_ui and ui_args.port == 8765 and ui_args.no_browser is False


def test_missing_pywebview_prints_the_install_hint(monkeypatch, capsys, track):
    monkeypatch.setitem(sys.modules, "webview", None)          # import fails
    assert cli.main(["desktop"]) == 1
    err = capsys.readouterr().err
    assert "Desktop mode needs pywebview." in err and 'pip install -e ".[desktop]"' in err
    assert "Traceback" not in err
    assert track.servers == [] and track.closes == 0          # nothing was started


def test_window_shows_the_local_server_then_everything_shuts_down(
        monkeypatch, track, no_outside):
    seen = {}

    def while_open(url, fake):
        status, page = _get(url)                               # the server is live
        seen["page"] = status == 200 and b"MAESTRO" in page
        seen["token"] = track.handlers[0].request_token.encode() in page
        seen["state"] = _get(url, "api/state")[0]
        seen["closes_while_open"] = track.closes

    fake = FakeWebview(during_start=while_open)
    _install(monkeypatch, fake)
    assert cli.main(["desktop"]) == 0

    host, port = track.address
    assert host == "127.0.0.1" and port == 0                   # loopback, auto port
    real_port = track.servers[0].server_address[1]
    assert real_port != 0
    (win,) = fake.windows
    assert win.title == "MAESTRO" and win.url == f"http://127.0.0.1:{real_port}/"
    assert win.kw["min_size"] == desktop.MIN_SIZE == (900, 600) and win.kw["resizable"] is True
    assert (win.kw["width"], win.kw["height"]) == (1280, 800)
    assert "js_api" not in win.kw                             # no Python exposed to JS
    assert fake.started == [{"debug": False}]
    assert fake.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True
    assert seen == {"page": True, "token": True, "state": 200, "closes_while_open": 0}
    # window closed: server stopped, socket closed, Workspace closed exactly once
    assert _refused(real_port)
    assert track.closes == 1
    # the existing Workspace and handler were used, once
    assert len(track.handlers) == 1 and isinstance(track.handlers[0], ui.Workspace)


def test_debug_is_opt_in(monkeypatch, track, no_outside):
    fake = FakeWebview()
    _install(monkeypatch, fake)
    assert cli.main(["desktop", "--debug"]) == 0
    assert fake.started == [{"debug": True}] and track.closes == 1


@pytest.mark.parametrize("where", ["create_window", "start"])
def test_cleanup_still_happens_when_pywebview_fails(monkeypatch, capsys, track, no_outside,
                                                    where):
    fake = FakeWebview(fail_on=where)
    _install(monkeypatch, fake)
    assert cli.main(["desktop"]) == 1
    err = capsys.readouterr().err
    assert "could not be opened" in err and "Traceback" not in err
    assert "X11" not in err and "QtWebEngine" not in err       # no internals
    assert _refused(track.servers[0].server_address[1])
    assert track.closes == 1


def test_window_is_sent_back_home_from_any_other_page(monkeypatch, track, no_outside):
    def while_open(url, fake):
        win = fake.windows[-1]
        for handler in win.events.loaded.handlers:
            handler()                                          # on MAESTRO: nothing to do
        assert win.loads == []
        win.current = "https://evil.example/phish"
        for handler in win.events.loaded.handlers:
            handler()
        assert win.loads == [url]

    _install(monkeypatch, FakeWebview(during_start=while_open))
    assert cli.main(["desktop"]) == 0


def test_a_recording_in_progress_is_released_when_the_window_closes(
        monkeypatch, track, no_outside):
    from maestro.voice import capture

    monkeypatch.setattr(capture, "_missing_deps", lambda: [])
    monkeypatch.setattr(capture.VoiceCapture, "_describe_device", lambda self: "Fake")
    mic = {"open": 0, "closed": 0}

    class Mic:
        transcriber = None

        def frames(self):
            import time

            import numpy as np
            mic["open"] += 1
            try:
                while True:
                    time.sleep(0.002)
                    yield np.where(np.arange(480) % 2, 0.05, -0.05).astype("float32")
            finally:
                mic["closed"] += 1

    def while_open(url, fake):
        ws = track.handlers[0]
        ws.voice._factory = Mic
        assert ws.voice.start()[0] == 202
        import time
        t0 = time.time()
        while ws.voice.status()["state"] != "listening" and time.time() - t0 < 5:
            time.sleep(0.01)
        assert ws.voice.status()["state"] == "listening"      # recording when the window closes

    _install(monkeypatch, FakeWebview(during_start=while_open))
    assert cli.main(["desktop"]) == 0
    assert mic == {"open": 1, "closed": 1} and track.closes == 1


def test_local_server_close_is_idempotent(track, no_outside):
    srv = ui.LocalServer(0)
    srv.start()
    assert _get(srv.url)[0] == 200
    srv.close()
    srv.close()
    assert track.closes == 1 and _refused(srv.httpd.server_address[1])


def test_maestro_ui_still_serves_in_the_foreground_and_cleans_up(monkeypatch, track,
                                                                 no_outside):
    opened = []
    monkeypatch.setattr(ui.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(ui.ThreadingHTTPServer, "serve_forever",
                        lambda self, *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    timers = []
    monkeypatch.setattr(ui.threading, "Timer",
                        lambda d, fn: types.SimpleNamespace(start=lambda: timers.append(fn)))
    assert ui.serve(0) == 0
    assert track.address == ("127.0.0.1", 0) and track.closes == 1
    assert len(timers) == 1                     # the browser would be opened...
    timers[0]()
    assert opened and opened[0].startswith("http://127.0.0.1:")
    assert ui.serve(0, open_browser=False) == 0 and len(timers) == 1   # ...unless told not to
    assert ui.DEFAULT_PORT == 8765
