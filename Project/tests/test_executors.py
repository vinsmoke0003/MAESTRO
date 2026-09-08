"""Executors: the four-method contract, and the promises each verb makes.

The recurring theme: an executor may fail, but it may never *lie*. A step that
half-worked returns ok=False with the partial manifest, so the orchestrator can
roll back what actually happened.
"""

from __future__ import annotations

import pytest

import maestro.executor  # noqa: F401
from maestro.executor.base import (
    Context,
    NotAvailable,
    UnboundVariable,
    get_executor,
    has_executor,
    registered_verbs,
    resolve,
)


def run(verb: str, args: dict, ctx: Context | None = None):
    return get_executor(verb).execute(args, ctx or Context())


def dry(verb: str, args: dict, ctx: Context | None = None):
    return get_executor(verb).dry_run(args, ctx or Context())


# =========================================================================== #
# variable resolution
# =========================================================================== #


def test_resolve_substitutes_bound_variables():
    ctx = Context()
    ctx.bind("files", ["a.pdf", "b.pdf"])
    assert resolve({"sources": "$files"}, ctx) == {"sources": ["a.pdf", "b.pdf"]}


def test_resolve_fails_closed_on_unbound_variables():
    """An unresolvable variable is a plan bug, not something to guess around."""
    with pytest.raises(UnboundVariable):
        resolve({"sources": "$never_bound"}, Context())


def test_resolve_leaves_literals_alone():
    assert resolve({"root": "~/Downloads", "n": 3}, Context()) == \
        {"root": "~/Downloads", "n": 3}


# =========================================================================== #
# fs.glob / list_dir / stat
# =========================================================================== #


def test_glob_finds_matching_files(files, workspace):
    files("inbox", "a.pdf", "b.pdf", "c.txt")
    result = run("fs.glob", {"root": str(workspace / "inbox"), "pattern": "*.pdf"})
    assert result.ok and len(result.output) == 2


def test_glob_on_a_missing_directory_returns_empty_not_an_error(workspace):
    result = run("fs.glob", {"root": str(workspace / "nope"), "pattern": "*"})
    assert result.ok and result.output == []


def test_glob_recursive(files, workspace):
    files("inbox", "a.pdf")
    files("inbox/sub", "b.pdf")
    shallow = run("fs.glob", {"root": str(workspace / "inbox"), "pattern": "*.pdf"})
    deep = run("fs.glob", {"root": str(workspace / "inbox"), "pattern": "*.pdf",
                           "recursive": True})
    assert len(shallow.output) == 1 and len(deep.output) == 2


def test_stat_reports_missing_files_as_failure(workspace):
    result = run("fs.stat", {"path": str(workspace / "ghost.txt")})
    assert not result.ok


# =========================================================================== #
# fs.read_text — the untrusted-content boundary
# =========================================================================== #


def test_read_text_marks_output_untrusted(files, workspace):
    files("inbox", "doc.txt", content="hello")
    result = run("fs.read_text", {"path": str(workspace / "inbox" / "doc.txt")})
    assert result.ok and result.output == "hello"
    # This flag is what stops the orchestrator handing it anywhere but the
    # Summarizer. Without it the trust model is a comment, not a mechanism.
    assert result.untrusted is True


def test_read_text_respects_max_bytes(files, workspace):
    files("inbox", "big.txt", content="x" * 5000)
    result = run("fs.read_text", {"path": str(workspace / "inbox" / "big.txt"),
                                  "max_bytes": 100})
    assert len(result.output) == 100


# =========================================================================== #
# fs.mkdir / copy / move — effects and undo
# =========================================================================== #


def test_mkdir_creates_and_undo_removes(workspace):
    target = workspace / "new" / "nested"
    ex = get_executor("fs.mkdir")
    result = ex.execute({"path": str(target)}, Context())
    assert result.ok and target.is_dir()
    ex.undo(result, Context())
    assert not target.exists()


def test_mkdir_undo_does_not_remove_a_directory_it_did_not_create(workspace):
    existing = workspace / "already"
    existing.mkdir()
    ex = get_executor("fs.mkdir")
    result = ex.execute({"path": str(existing)}, Context())
    ex.undo(result, Context())
    assert existing.is_dir()  # untouched — we did not create it


def test_mkdir_undo_leaves_a_non_empty_directory_alone(workspace):
    target = workspace / "made"
    ex = get_executor("fs.mkdir")
    result = ex.execute({"path": str(target)}, Context())
    (target / "someone_elses.txt").write_text("data")
    ex.undo(result, Context())
    assert target.is_dir()  # undo must not destroy data that arrived later


def test_copy_never_overwrites(files, workspace):
    (src,) = files("inbox", "a.txt", content="new")
    dest_dir = workspace / "out"
    dest_dir.mkdir()
    (dest_dir / "a.txt").write_text("original")
    result = run("fs.copy", {"src": str(src), "dst": str(dest_dir / "a.txt")})
    assert result.ok
    assert (dest_dir / "a.txt").read_text() == "original"
    assert (dest_dir / "a (1).txt").read_text() == "new"


def test_move_batch_moves_and_undo_restores(files, workspace):
    made = files("inbox", "a.pdf", "b.pdf")
    dest = workspace / "archive"
    ex = get_executor("fs.move_batch")
    result = ex.execute({"sources": [str(p) for p in made], "dest_dir": str(dest)},
                        Context())
    assert result.ok
    assert not made[0].exists() and (dest / "a.pdf").exists()

    ex.undo(result, Context())
    assert made[0].exists() and not (dest / "a.pdf").exists()


def test_move_batch_halts_on_a_missing_source_and_keeps_the_manifest(files, workspace):
    """The partial manifest is what makes rollback possible after a half-move."""
    made = files("inbox", "a.pdf")
    sources = [str(made[0]), str(workspace / "inbox" / "ghost.pdf")]
    ex = get_executor("fs.move_batch")
    result = ex.execute({"sources": sources, "dest_dir": str(workspace / "out")},
                        Context())
    assert not result.ok
    assert len(result.undo_data) == 1        # the one that did move
    ex.undo(result, Context())
    assert made[0].exists()                  # and it came back


def test_move_batch_renames_on_collision(files, workspace):
    (src,) = files("inbox", "a.pdf", content="incoming")
    dest = workspace / "archive"
    dest.mkdir()
    (dest / "a.pdf").write_text("already here")
    result = run("fs.move_batch", {"sources": [str(src)], "dest_dir": str(dest)})
    assert result.ok
    assert (dest / "a.pdf").read_text() == "already here"
    assert (dest / "a (1).pdf").read_text() == "incoming"


def test_copy_batch_leaves_the_originals(files, workspace):
    made = files("inbox", "a.txt", "b.txt")
    result = run("fs.copy_batch", {"sources": [str(p) for p in made],
                                   "dest_dir": str(workspace / "backup")})
    assert result.ok
    assert all(p.exists() for p in made)
    assert len(list((workspace / "backup").glob("*.txt"))) == 2


def test_rename_refuses_to_become_a_move(files, workspace):
    """A rename that accepts a path is a move that dodged the destination's
    path check."""
    (src,) = files("inbox", "a.txt")
    result = run("fs.rename", {"path": str(src), "new_name": "../../escaped.txt"})
    assert not result.ok
    assert "bare filename" in result.detail


def test_restore_manifest_is_the_declared_inverse(files, workspace):
    made = files("inbox", "a.pdf", "b.pdf")
    dest = workspace / "archive"
    moved = run("fs.move_batch", {"sources": [str(p) for p in made],
                                  "dest_dir": str(dest)})
    restored = run("fs.restore_manifest", {"manifest": moved.undo_data})
    assert restored.ok and len(restored.output["restored"]) == 2
    assert restored.output["mismatched"] == []       # bytes verified against move-time hash
    assert all(p.exists() for p in made)


def test_move_manifest_records_a_content_hash(files, workspace):
    """The hash is what lets `undo` PROVE a restore rather than assert it."""
    (src,) = files("inbox", "a.pdf", content="exact bytes")
    result = run("fs.move_batch", {"sources": [str(src)],
                                   "dest_dir": str(workspace / "out")})
    entry = result.undo_data[0]
    assert set(entry) == {"from", "to", "sha256"}
    assert len(entry["sha256"]) == 64


def test_restore_reports_a_content_mismatch_instead_of_hiding_it(files, workspace):
    (src,) = files("inbox", "a.pdf", content="original")
    moved = run("fs.move_batch", {"sources": [str(src)],
                                  "dest_dir": str(workspace / "out")})
    # Tamper with the moved file before restoring it.
    (workspace / "out" / "a.pdf").write_text("tampered")
    restored = run("fs.restore_manifest", {"manifest": moved.undo_data})
    assert not restored.ok
    assert len(restored.output["mismatched"]) == 1
    assert "differ from their move-time hash" in restored.detail


# =========================================================================== #
# dry runs — the basis of meaningful consent
# =========================================================================== #


def test_dry_run_reports_real_counts_and_bytes(files, workspace):
    files("inbox", "a.pdf", "b.pdf", content="x" * 100)
    manifest = dry("fs.glob", {"root": str(workspace / "inbox"), "pattern": "*.pdf"})
    assert manifest.files_touched == 2
    assert manifest.bytes_affected == 200


def test_dry_run_surfaces_collisions(files, workspace):
    (src,) = files("inbox", "a.pdf")
    dest = workspace / "archive"
    dest.mkdir()
    (dest / "a.pdf").write_text("existing")
    manifest = dry("fs.move_batch", {"sources": [str(src)], "dest_dir": str(dest)})
    assert manifest.collisions and "never overwritten" in manifest.collisions[0]


def test_dry_run_changes_nothing(files, workspace):
    made = files("inbox", "a.pdf")
    before = {p: p.read_text() for p in made}
    dry("fs.move_batch", {"sources": [str(made[0])],
                          "dest_dir": str(workspace / "out")})
    dry("fs.trash", {"paths": [str(made[0])]})
    assert all(p.exists() and p.read_text() == before[p] for p in made)


def test_read_text_dry_run_declares_the_content_untrusted(files, workspace):
    files("inbox", "doc.txt")
    manifest = dry("fs.read_text", {"path": str(workspace / "inbox" / "doc.txt")})
    assert any("UNTRUSTED" in u for u in manifest.unknowns)


def test_unpredictable_effects_are_declared_not_omitted():
    """docs/06 §3: an honest "I can't predict this" is safe; a silently
    incomplete preview is not."""
    manifest = dry("search.by_name", {"root": "~/Downloads", "query": "x"})
    assert manifest.unknowns


# =========================================================================== #
# search
# =========================================================================== #


def test_search_by_name(files, workspace):
    files("docs", "budget_2026.xlsx", "notes.txt")
    result = run("search.by_name", {"root": str(workspace), "query": "budget"})
    assert result.ok and len(result.output) == 1


def test_search_by_content_marks_matches_untrusted(files, workspace):
    files("docs", "a.txt", content="the secret word is platypus")
    result = run("search.by_content", {"root": str(workspace), "query": "platypus"})
    assert result.ok and len(result.output) == 1
    assert result.untrusted is True


def test_search_recent_filters_by_mtime(files, workspace):
    import os
    import time

    made = files("docs", "old.txt", "new.txt")
    ancient = time.time() - 400 * 86400
    os.utime(made[0], (ancient, ancient))
    result = run("search.recent", {"root": str(workspace), "days": 7})
    assert [p for p in result.output if "new.txt" in p]
    assert not [p for p in result.output if "old.txt" in p]


# =========================================================================== #
# browser — optional dependency, credential guard
# =========================================================================== #


def test_browser_refuses_credential_fields():
    """A hard block, not a heuristic warning (docs/06 §5)."""
    result = run("browser.fill", {"url": "https://example.com",
                                  "selector": "#password", "value": "hunter2"})
    assert not result.ok
    assert "never enters passwords" in result.detail


def test_browser_refuses_non_http_urls():
    result = run("browser.open", {"url": "file:///etc/passwd"})
    assert not result.ok and "http" in result.detail


def test_browser_click_cannot_be_undone():
    with pytest.raises(NotImplementedError):
        get_executor("browser.click").undo(
            maestro.executor.Result(ok=True), Context())


def test_missing_playwright_degrades_to_a_step_failure(monkeypatch):
    """A missing optional backend must fail the step, never the process.

    Simulated where the new code actually fails: `BrowserSession.start()` is
    the single place the browser is launched, so making it raise NotAvailable
    is exactly what an uninstalled Playwright looks like to every verb.
    """
    import maestro.executor.browser as browser_mod

    def boom(self):
        raise NotAvailable("Playwright is not installed")

    monkeypatch.setattr(browser_mod.BrowserSession, "start", boom)
    result = run("browser.open", {"url": "https://example.com"})
    assert not result.ok
    assert "not installed" in result.detail


# =========================================================================== #
# draft — the useful half of a blocked capability
# =========================================================================== #


def test_draft_email_writes_an_unsent_eml(workspace):
    result = run("draft.email", {"to": ["guide@amity.edu"], "subject": "Progress",
                                 "body": "All good."})
    assert result.ok
    path = workspace / "drafts"
    written = list(path.glob("*.eml"))
    assert len(written) == 1
    content = written[0].read_text(encoding="utf-8", errors="replace")
    assert "guide@amity.edu" in content
    assert "X-MAESTRO-Draft" in content
    assert "not sent" in result.detail


def test_draft_email_undo_removes_the_file(workspace):
    ex = get_executor("draft.email")
    result = ex.execute({"to": ["a@b.c"], "subject": "S", "body": "B"}, Context())
    ex.undo(result, Context())
    assert not list((workspace / "drafts").glob("*.eml"))


def test_email_send_has_no_executor_at_all():
    """docs/06 §5: the irreversible half does not exist as an executable verb.
    A plan that wants to send mail cannot be written, not merely refused."""
    assert not has_executor("email.send")
    assert not has_executor("fs.delete_permanent")
    assert "email.send" not in registered_verbs()


def test_draft_note_writes_markdown(workspace):
    result = run("draft.note", {"title": "Meeting", "body": "Notes here"})
    assert result.ok
    written = list((workspace / "drafts").glob("*.md"))
    assert len(written) == 1 and "Notes here" in written[0].read_text()


# =========================================================================== #
# system
# =========================================================================== #


def test_sys_info_disk_works_everywhere():
    result = run("sys.info", {"metric": "disk", "path": "~"})
    assert result.ok and "free_gb" in result.output


def test_sys_info_rejects_an_unknown_metric():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        run("sys.info", {"metric": "everything"})


def test_unavailable_backend_is_a_failure_not_a_silent_noop():
    """T8: a no-op that reports success is the failure mode we exist to prevent."""
    result = run("app.launch", {"app_id": "definitely-not-installed-xyz"})
    assert not result.ok
