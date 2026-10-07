"""`maestro desktop`: the workspace in a native window (pywebview).

This is a shell, not a second application. It starts the same `LocalServer`
`maestro ui` uses (one Workspace, the existing handler, 127.0.0.1 only, a free
port) on a background thread, and points one native window at it. Everything
the page does still goes through the HTTP API, so the request token, the Host
and Origin checks, the consent gate, Google and voice behave exactly as in a
browser. No Python function is exposed to the page's JavaScript.

Google sign-in still opens in the system browser: `auth.connect()` launches it
with `webbrowser`, never inside this window. The window only ever shows
MAESTRO's own page; anything else it is navigated to is sent back home, and
external links open in the system browser.

When the window closes (or the window cannot be created), the server is
stopped, its socket closed, a running recording released, and the Workspace
closed, exactly once.
"""

from __future__ import annotations

import sys

TITLE = "MAESTRO"
SIZE = (1280, 800)
MIN_SIZE = (900, 600)

MISSING = ('Desktop mode needs pywebview.\n'
           'Install it with: pip install -e ".[desktop]"')


def _load_webview():
    """pywebview, or None if it is not installed (imported only when asked for)."""
    try:
        import webview
    except ImportError:
        return None
    return webview


def _keep_local(window, home: str) -> None:
    """If the window ever shows a page that is not MAESTRO's own, go back to it."""
    try:
        current = window.get_current_url() or ""
        if not current.startswith(home):
            window.load_url(home)
    except Exception:  # noqa: BLE001 - a best-effort guard must not break the window
        pass


def run(*, port: int = 0, debug: bool = False) -> int:
    """Open the native window over a fresh local server; return when it is closed."""
    webview = _load_webview()
    if webview is None:
        print(MISSING, file=sys.stderr)
        return 1

    from maestro.ui import LocalServer

    try:
        srv = LocalServer(port)
    except OSError:
        print(f"MAESTRO could not listen on 127.0.0.1:{port}. Try another --port.",
              file=sys.stderr)
        return 1
    try:
        srv.start()
        settings = getattr(webview, "settings", None)
        if isinstance(settings, dict):
            settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
            settings["ALLOW_DOWNLOADS"] = False
        window = webview.create_window(
            TITLE, srv.url, width=SIZE[0], height=SIZE[1], min_size=MIN_SIZE,
            resizable=True,
        )
        home = srv.url
        try:
            window.events.loaded += lambda *a: _keep_local(window, home)
        except AttributeError:
            pass
        webview.start(debug=debug)            # blocks until the window is closed
        return 0
    except Exception:  # noqa: BLE001 - no traceback for a window that cannot open
        print("The MAESTRO window could not be opened. Try `maestro ui` to use it in a "
              "browser instead.", file=sys.stderr)
        return 1
    finally:
        srv.close()


__all__ = ["run", "TITLE", "SIZE", "MIN_SIZE"]
