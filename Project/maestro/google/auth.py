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


def google_home() -> Path:
    """Where MAESTRO keeps the Google client file and token (~/.maestro/google). Never created
    here, so inspecting status cannot change the disk.
    """
    return settings().home / "google"


def google_dir() -> Path:
    """The Google folder, created if needed. Only for operations that write into it."""
    d = google_home()
    d.mkdir(parents=True, exist_ok=True)
    return d


def client_secret_path() -> Path:
    """The OAuth client file downloaded from Google Cloud Console (not created)."""
    override = os.environ.get("MAESTRO_GOOGLE_CLIENT_SECRET")
    return Path(override).expanduser() if override else google_home() / "client_secret.json"


def token_path() -> Path:
    """Where the sign-in token is stored (not created)."""
    return google_home() / "token.json"


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
    google_dir()
    p = token_path()
    p.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def connect(open_browser: bool = True, timeout_seconds: int | None = None):
    """Run the browser consent flow and store the token. Returns credentials.

    `timeout_seconds` ends a sign-in the user abandoned (None waits forever, as the CLI does).
    """
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
                                                   "account. You can close this tab."),
                                  timeout_seconds=timeout_seconds)
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


# --------------------------------------------------------------------------- #
# read-only status for the workspace UI
# --------------------------------------------------------------------------- #

# Which granted scopes each service needs. Scope names are public identifiers,
# not credentials.
SERVICE_SCOPES = {
    "gmail": ["gmail.readonly", "gmail.compose"],
    "drive": ["drive.readonly", "drive.file"],
    "calendar": ["calendar.events"],
}

_STATE_MESSAGES = {
    "setup_required": "Google is not set up yet. Create the OAuth client file "
                      "(see docs/GOOGLE-SETUP.md), then run: maestro google connect",
    "connect_required": "The OAuth client is ready. Click Connect Google, or run: "
                        "maestro google connect",
    "connected": "Connected to Google.",
    "reconnect_required": "The Google sign-in needs to be renewed. Run: maestro google connect",
}


def _libraries_installed() -> bool:
    """True if Google's client libraries can be imported (checked without importing them)."""
    import importlib.util

    try:
        return all(importlib.util.find_spec(m) is not None
                   for m in ("googleapiclient", "google_auth_oauthlib"))
    except (ImportError, ValueError):
        return False


def _short_scopes(raw) -> list[str]:
    """Scope URLs as short names ('gmail.readonly'); accepts a list or a space-separated string."""
    items = raw.split() if isinstance(raw, str) else list(raw or [])
    return sorted({str(s).rsplit("/", 1)[-1] for s in items if s})


def _expired(expiry: str | None) -> bool | None:
    """Whether a token's expiry time has passed; None if unknown or unreadable."""
    if not expiry:
        return None
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)   # google-auth writes UTC expiry times
    return when <= datetime.now(timezone.utc)


def integration_status() -> dict:
    """Google status for the workspace UI, read from local files only.

    Never creates folders or files, never refreshes the token, never touches the
    network. Returns booleans, short scope names and the token's expiry time;
    no secret, token value or file path ever leaves this function.
    """
    libraries = _libraries_installed()
    client_ok = client_secret_path().is_file()
    tok = token_path()
    token_present = tok.is_file()
    token_readable, scopes, expiry, refreshable = False, [], None, False
    if token_present:
        try:
            data = json.loads(tok.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                scopes = _short_scopes(data.get("scopes"))
                expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None
                refreshable = bool(data.get("refresh_token"))
                token_readable = True
            del data
        except (OSError, ValueError):
            token_readable = False
    expired = _expired(expiry)

    if token_present and libraries:
        usable = token_readable and (refreshable or expired is False)
        all_scopes = all(s in scopes for need in SERVICE_SCOPES.values() for s in need)
        state = "connected" if usable and all_scopes else "reconnect_required"
    elif client_ok and libraries:
        state = "connect_required"
    else:
        state = "setup_required"

    reason = None
    if not libraries:
        reason = 'Google support is not installed: pip install -e ".[google]"'
    elif state == "reconnect_required":
        if not token_readable:
            reason = "The stored sign-in could not be read."
        elif not (refreshable or expired is False):
            reason = "The stored sign-in has expired and cannot be renewed."
        else:
            reason = "Some permissions were not granted."

    services = {}
    for name, need in SERVICE_SCOPES.items():
        if state in ("setup_required", "connect_required"):
            svc = state
        elif state == "reconnect_required" and not (token_readable and
                                                    (refreshable or expired is False)):
            svc = "reconnect_required"
        else:
            svc = "connected" if all(s in scopes for s in need) else "permission_missing"
        services[name] = {"state": svc, "permission": all(s in scopes for s in need),
                          "scopes": [s for s in need if s in scopes]}

    return {
        "state": state,
        "message": reason or _STATE_MESSAGES[state],
        "libraries_installed": libraries,
        "client_configured": client_ok,
        "token_present": token_present,
        "token_readable": token_readable,
        "token_expiry": expiry,
        "token_expired": expired,
        "token_refreshable": refreshable,
        "scopes": scopes,
        "services": services,
    }


def disconnect() -> bool:
    """Revoke the token at Google (best effort) and delete it locally. False if there was none."""
    return disconnect_detailed()["removed"]


def disconnect_detailed() -> dict:
    """Revoke the token at Google (best effort), then delete it locally.

    Returns {"had_token", "revoked", "removed"}: revoked is None when there was nothing to
    revoke. A failed revocation never stops the local token from being deleted. Touches only
    the token file: no mail, Drive file or calendar event is read or changed.
    """
    p = token_path()
    if not p.exists():
        return {"had_token": False, "revoked": None, "removed": False}
    revoked = None
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
            revoked = True
    except Exception:
        revoked = False  # revocation is best effort; deleting the token is what matters here
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
    return {"had_token": True, "revoked": revoked, "removed": not p.exists()}
