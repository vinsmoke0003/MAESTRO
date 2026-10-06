"""Google sign-in for MAESTRO: OAuth 2.0 for an installed (desktop) app.

    maestro google connect      opens the browser; YOU sign in and approve
    maestro google status       which account, which permissions
    maestro google disconnect   revokes the token at Google and deletes it here

MAESTRO never sees your Google password. Google's own consent page asks you to
approve the exact scopes below, and hands back a token that is stored on this
machine only, readable only by you (mode 0600), under MAESTRO_HOME — which the
path policy denylists, so no plan can read, move or upload it.

Scopes are the narrowest that cover the chosen features:

    gmail.readonly   search and read your mail
    gmail.compose    create DRAFTS. Google bundles "send" into this scope; there
                     is no drafts-only scope. MAESTRO has no code path that
                     sends: `email.send` is hard-blocked in the registry and has
                     no executor, so the scope's send permission is unreachable.
    drive.readonly   search and download your Drive files
    drive.file       upload files — and touch ONLY files MAESTRO itself
                     uploaded (used to undo an upload). It cannot modify,
                     share or delete anything else in your Drive.
    calendar.events  add reminders to your calendar. MAESTRO only ever creates
                     events on your own primary calendar, with no guests (so no
                     invitation email is ever sent), and only deletes events it
                     created itself, to undo them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from maestro.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar.events",
]


class GoogleNotConnected(RuntimeError):
    """No usable token: the user has not run `maestro google connect`."""


def google_dir() -> Path:
    """The folder where MAESTRO keeps the Google client file and token (~/.maestro/google), created
    if needed.
    """
    d = settings().home / "google"
    d.mkdir(parents=True, exist_ok=True)
    return d


def client_secret_path() -> Path:
    """The OAuth client file downloaded from Google Cloud Console."""
    override = os.environ.get("MAESTRO_GOOGLE_CLIENT_SECRET")
    return Path(override).expanduser() if override else google_dir() / "client_secret.json"


def token_path() -> Path:
    """Where the sign-in token is stored."""
    return google_dir() / "token.json"


def _libs():
    """Import Google's sign-in libraries, or explain how to install them."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise GoogleNotConnected(
            "Google support is not installed: pip install -e \".[google]\""
        ) from e
    return Request, Credentials, InstalledAppFlow


def _save(creds) -> None:
    """Write the token to disk, readable only by the current user."""
    p = token_path()
    p.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def connect(open_browser: bool = True):
    """Run the browser consent flow and store the token. Returns credentials."""
    _, _, InstalledAppFlow = _libs()
    secret = client_secret_path()
    if not secret.exists():
        raise GoogleNotConnected(
            f"no OAuth client file at {secret}. Create a Desktop OAuth client in "
            "Google Cloud Console and save its JSON there (see "
            "Project/docs/GOOGLE-SETUP.md)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    # Loopback redirect on a random free port on 127.0.0.1: the token comes
    # straight back to this process and never passes through any server of ours.
    creds = flow.run_local_server(port=0, open_browser=open_browser,
                                  authorization_prompt_message=(
                                      "Opening your browser to sign in to Google...\n"
                                      "If it does not open, visit:\n{url}\n"),
                                  success_message=("MAESTRO is connected to your Google "
                                                   "account. You can close this tab."))
    _save(creds)
    return creds


def credentials():
    """Load the stored token, refreshing it if it expired. Raises if absent."""
    Request, Credentials, _ = _libs()
    p = token_path()
    if not p.exists():
        raise GoogleNotConnected("Google is not connected. Run: maestro google connect")
    creds = Credentials.from_authorized_user_file(str(p), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise GoogleNotConnected(
                    f"the Google sign-in expired and could not be renewed ({e}). "
                    "Run: maestro google connect") from e
            _save(creds)
        else:
            raise GoogleNotConnected("the Google sign-in is no longer valid. "
                                     "Run: maestro google connect")
    return creds


def status() -> dict:
    """What is configured, without making network calls."""
    p = token_path()
    info = {"client_secret": client_secret_path().exists(), "connected": p.exists(),
            "scopes": []}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            info["scopes"] = data.get("scopes", [])
            info["expiry"] = data.get("expiry")
        except (OSError, ValueError):
            info["connected"] = False
    return info


def disconnect() -> bool:
    """Revoke the token at Google (best effort) and delete it locally."""
    p = token_path()
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        token = data.get("refresh_token") or data.get("token")
        if token:
            import urllib.parse
            import urllib.request

            req = urllib.request.Request(
                "https://oauth2.googleapis.com/revoke",
                data=urllib.parse.urlencode({"token": token}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            urllib.request.urlopen(req, timeout=10)  # noqa: S310 - fixed Google URL
    except Exception:
        pass  # revocation is best effort; deleting the token is what matters here
    p.unlink(missing_ok=True)
    return True
