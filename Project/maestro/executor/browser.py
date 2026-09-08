"""Browser verbs (PRD task category T3), via Playwright.

    browser.open      R1  open a URL in the plan's browser session
    browser.extract   R0  extract text from a selector  -> UNTRUSTED output
    browser.click     R2  click an element              (irreversible in practice)
    browser.fill      R2  type into a field             (irreversible in practice)
    browser.download  R2  download a URL to a local path

**One session per plan.** The first version launched a fresh browser inside every
verb and closed it on the way out, so `browser.open` → `browser.fill` →
`browser.click` shared nothing: the page the user filled was gone before the
click. Now the executors share a `BrowserSession` that lives on the execution
`Context`; it is created lazily by the first verb that needs it, every later verb
reuses its page (URL, cookies, filled fields, all intact), and the orchestrator
closes it when the plan ends — success, failure or rollback. That is the
lifecycle boundary: a plan, not a verb.

`url` is still an argument to every verb so a plan remains statically checkable
(the scorer sees every destination up front). At run time a verb navigates only
if the session is not already on that URL, which is what lets the multi-step
form workflow be one continuous interaction.

Playwright is an optional dependency. Every executor raises `NotAvailable`,
which the orchestrator turns into a clean step failure — a missing browser
degrades the task, never the process.

Two safety facts specific to this module:

* `browser.click` / `browser.fill` are marked **irreversible**. A submitted form
  cannot be un-submitted; docs/06 §7 says so explicitly and the registry agrees,
  so the scorer pushes them to R3 unless the plan declares an undo (it cannot).
* Credential entry is not reachable through `browser.fill`: the field-name
  guard refuses password / card / OTP-shaped selectors outright. That is a hard
  block (docs/06 §5), not a heuristic warning.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from maestro.executor.base import (
    Context,
    EffectManifest,
    NotAvailable,
    Result,
    register_executor,
    resolve,
)
from maestro.ir import Risk
from maestro.registry import VerbSpec, register

# Selector / field patterns that would make MAESTRO a credential-entry tool.
CREDENTIAL_PATTERNS = [
    r"pass(word|wd|phrase)",
    r"\bpin\b",
    r"\botp\b",
    r"one[-_ ]?time",
    r"cvv|cvc|card[-_ ]?number|cardnum",
    r"ssn|aadhaar|aadhar|passport",
    r"secret|api[-_ ]?key|token",
]
_CRED_RE = re.compile("|".join(CREDENTIAL_PATTERNS), re.I)

NAV_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 15_000


class OpenArgs(BaseModel):
    url: str
    headless: bool = True


class ExtractArgs(BaseModel):
    url: str
    selector: str = "body"
    limit: int = Field(default=20, ge=1, le=500)


class ClickArgs(BaseModel):
    url: str
    selector: str


class FillArgs(BaseModel):
    url: str
    selector: str
    value: str


class DownloadArgs(BaseModel):
    url: str
    dest: str


register(VerbSpec("browser.open", OpenArgs, Risk.R1, reversible=True, category="browser",
                  description="Open a URL in the plan's browser session"))
register(VerbSpec("browser.extract", ExtractArgs, Risk.R0, reversible=True,
                  category="browser",
                  description="Extract text from a page (output is UNTRUSTED content)"))
register(VerbSpec("browser.click", ClickArgs, Risk.R2, reversible=False, category="browser",
                  description="Click an element on a page (not reversible)"))
register(VerbSpec("browser.fill", FillArgs, Risk.R2, reversible=False, category="browser",
                  description="Type a value into a form field (not reversible)",
                  sensitive_args=("value",)))
register(VerbSpec("browser.download", DownloadArgs, Risk.R2, reversible=True,
                  category="browser", network_write=False,
                  description="Download a URL to a local file", path_args=("dest",),
                  sensitive_args=("dest",)))


# --------------------------------------------------------------------------- #
# the session
# --------------------------------------------------------------------------- #


class BrowserSession:
    """One Playwright browser + one page, shared by every browser verb in a plan.

    Created lazily so a plan with no browser verbs never pays for a launch, and
    closed exactly once by `Context.close()` when the plan is over.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self._browser = None
        self._page = None
        self.visited: list[str] = []

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError as e:
            raise NotAvailable(
                "Playwright is not installed. `pip install playwright && playwright "
                "install chromium` enables the browser verbs; every other category "
                "works without it."
            ) from e
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
        except Exception as e:
            self._pw.stop()
            self._pw = None
            raise NotAvailable(
                f"Playwright is installed but no browser binary is — run "
                f"`playwright install chromium` ({str(e)[:80]})"
            ) from e
        self._page = self._browser.new_page()

    def close(self) -> None:
        for closer in (lambda: self._browser.close() if self._browser else None,
                       lambda: self._pw.stop() if self._pw else None):
            try:
                closer()
            except Exception:
                pass
        self._browser = self._pw = self._page = None

    @property
    def open(self) -> bool:
        return self._page is not None

    # -- navigation --------------------------------------------------------

    def goto(self, url: str) -> None:
        """Navigate only if we are not already there. Re-loading the page a
        user just filled in would throw away exactly the state the session
        exists to keep."""
        self.start()
        assert self._page is not None
        if _same_url(self._page.url, url):
            return
        self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        self.visited.append(url)

    @property
    def page(self):
        self.start()
        return self._page

    @property
    def url(self) -> str:
        return self._page.url if self._page is not None else ""


def _same_url(a: str, b: str) -> bool:
    norm = lambda u: (u or "").rstrip("/").lower()  # noqa: E731
    return norm(a) == norm(b)


def session_for(ctx: Context, headless: bool = True) -> BrowserSession:
    """The plan's session, created on first use and remembered on the Context."""
    sess = getattr(ctx, "session", None)
    if sess is None:
        sess = BrowserSession(headless=headless)
        ctx.session = sess
    return sess


# --------------------------------------------------------------------------- #
# URL policy
# --------------------------------------------------------------------------- #


def _check_url(url: str) -> str | None:
    if not re.match(r"^https?://", url, re.I):
        return "only http(s) URLs are allowed"
    # Loopback is blocked so a plan cannot poke local services (a database
    # admin page, a dev server) through the browser verbs. The tests need a
    # local fixture server and opt in explicitly — never the default.
    if re.match(r"^https?://(localhost|127\.|0\.0\.0\.0|\[::1\])", url, re.I) \
            and os.environ.get("MAESTRO_ALLOW_LOOPBACK") not in ("1", "true"):
        return "loopback URLs are not reachable through the browser verbs"
    return None


def _fail(e: Exception) -> Result:
    return Result(ok=False, detail=f"{type(e).__name__}: {str(e)[:200]}")


# --------------------------------------------------------------------------- #
# executors
# --------------------------------------------------------------------------- #


class OpenExecutor:
    verb = "browser.open"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = OpenArgs.model_validate(resolve(args, ctx))
        return EffectManifest(summary=f"Open {a.url}", external=[a.url],
                              unknowns=["page content is UNTRUSTED"])

    def execute(self, args: dict, ctx: Context) -> Result:
        a = OpenArgs.model_validate(resolve(args, ctx))
        bad = _check_url(a.url)
        if bad:
            return Result(ok=False, detail=bad)
        try:
            sess = session_for(ctx, a.headless)
            sess.goto(a.url)
            title = sess.page.title()
        except NotAvailable as e:
            return Result(ok=False, detail=str(e))
        except Exception as e:
            return _fail(e)
        return Result(ok=True, output={"url": sess.url, "title": title},
                      detail=f"opened {a.url} — {title!r}", undo_data={"opened": a.url})

    def undo(self, result: Result, ctx: Context) -> None:
        pass  # the session is closed at the plan boundary, not per verb


class ExtractExecutor:
    verb = "browser.extract"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = ExtractArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Extract {a.selector!r} from {a.url}",
            external=[a.url],
            unknowns=["extracted text is UNTRUSTED and never reaches the planner"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = ExtractArgs.model_validate(resolve(args, ctx))
        bad = _check_url(a.url)
        if bad:
            return Result(ok=False, detail=bad)
        try:
            sess = session_for(ctx)
            sess.goto(a.url)
            nodes = sess.page.query_selector_all(a.selector)[: a.limit]
            texts = [(n.inner_text() or "").strip() for n in nodes]
        except NotAvailable as e:
            return Result(ok=False, detail=str(e))
        except Exception as e:
            return _fail(e)
        return Result(ok=True, output=texts, detail=f"{len(texts)} node(s)", untrusted=True)

    def undo(self, result: Result, ctx: Context) -> None:
        pass


class ClickExecutor:
    verb = "browser.click"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = ClickArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Click {a.selector!r} on {a.url}",
            external=[a.url],
            unknowns=["clicking may submit a form; this is NOT reversible"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = ClickArgs.model_validate(resolve(args, ctx))
        bad = _check_url(a.url)
        if bad:
            return Result(ok=False, detail=bad)
        try:
            sess = session_for(ctx)
            sess.goto(a.url)
            before = sess.url
            sess.page.click(a.selector, timeout=ACTION_TIMEOUT_MS)
            # A click that submits usually navigates; give it a moment so the
            # postcondition sees the page the user would.
            try:
                sess.page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except Exception:
                pass
            after = sess.url
        except NotAvailable as e:
            return Result(ok=False, detail=str(e))
        except Exception as e:
            return _fail(e)
        moved = "" if _same_url(before, after) else f"; navigated to {after}"
        return Result(ok=True, output={"selector": a.selector, "url": after},
                      detail=f"clicked {a.selector}{moved}")

    def undo(self, result: Result, ctx: Context) -> None:
        raise NotImplementedError("a click cannot be un-clicked")


class FillExecutor:
    verb = "browser.fill"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = FillArgs.model_validate(resolve(args, ctx))
        cred = _CRED_RE.search(a.selector)
        return EffectManifest(
            summary=f"Type into {a.selector!r} on {a.url}",
            external=[a.url],
            unknowns=(["REFUSED: this field looks like a credential field"] if cred
                      else ["typing into a form is NOT reversible"]),
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        a = FillArgs.model_validate(resolve(args, ctx))
        if _CRED_RE.search(a.selector) or _CRED_RE.search(a.value[:120]):
            # Hard block, docs/06 §5. No override flag exists on purpose.
            return Result(
                ok=False,
                detail="refused: MAESTRO never enters passwords, PINs, card numbers, "
                       "OTPs or government IDs into any field",
            )
        bad = _check_url(a.url)
        if bad:
            return Result(ok=False, detail=bad)
        try:
            sess = session_for(ctx)
            sess.goto(a.url)
            sess.page.fill(a.selector, a.value, timeout=ACTION_TIMEOUT_MS)
            # Read the field back: the postcondition for "filled" is the value
            # actually being in the field, not the call returning.
            got = sess.page.input_value(a.selector, timeout=ACTION_TIMEOUT_MS)
        except NotAvailable as e:
            return Result(ok=False, detail=str(e))
        except Exception as e:
            return _fail(e)
        if got != a.value:
            return Result(ok=False, detail=f"field {a.selector} holds {got!r}, "
                                            f"expected {a.value!r}")
        return Result(ok=True, output={"selector": a.selector, "value": got},
                      detail=f"filled {a.selector} (verified)")

    def undo(self, result: Result, ctx: Context) -> None:
        raise NotImplementedError("a form entry cannot be reliably un-typed")


class DownloadExecutor:
    verb = "browser.download"

    def dry_run(self, args: dict, ctx: Context) -> EffectManifest:
        a = DownloadArgs.model_validate(resolve(args, ctx))
        return EffectManifest(
            summary=f"Download {a.url} -> {a.dest}",
            external=[a.url],
            creates=[a.dest],
            files_touched=1,
            unknowns=["the size and content of a remote file are unknown before fetching"],
        )

    def execute(self, args: dict, ctx: Context) -> Result:
        import urllib.request

        a = DownloadArgs.model_validate(resolve(args, ctx))
        bad = _check_url(a.url)
        if bad:
            return Result(ok=False, detail=bad)
        dest = Path(a.dest).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(a.url, timeout=60) as resp:  # noqa: S310
                data: Any = resp.read()
        except Exception as e:
            return _fail(e)
        dest.write_bytes(data)
        # Postcondition by construction: the file exists with the bytes we got.
        if not dest.is_file() or dest.stat().st_size != len(data):
            return Result(ok=False, detail=f"{dest} was not written completely")
        return Result(ok=True, output=str(dest), files_touched=1, untrusted=True,
                      detail=f"downloaded {len(data)} bytes to {dest.name}",
                      undo_data={"downloaded": str(dest)})

    def undo(self, result: Result, ctx: Context) -> None:
        d = result.undo_data or {}
        if d.get("downloaded"):
            Path(d["downloaded"]).unlink(missing_ok=True)


for _ex in (OpenExecutor(), ExtractExecutor(), ClickExecutor(), FillExecutor(),
            DownloadExecutor()):
    register_executor(_ex)
