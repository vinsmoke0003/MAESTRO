"""Path policy — including the traversal and symlink cases from docs/06 §2."""

from pathlib import Path

from maestro.safety.paths import PathPolicy, PathVerdict


def policy(tmp_path: Path) -> PathPolicy:
    return PathPolicy(
        allow_roots=[str(tmp_path / "workspace")],
        deny_dirs=[str(tmp_path / "secrets")],
    )


def test_allowed(tmp_path):
    p = policy(tmp_path)
    assert p.check(tmp_path / "workspace" / "a.txt") == PathVerdict.ALLOWED


def test_outside(tmp_path):
    p = policy(tmp_path)
    assert p.check(tmp_path / "elsewhere" / "a.txt") == PathVerdict.OUTSIDE


def test_denied_dir(tmp_path):
    p = policy(tmp_path)
    assert p.check(tmp_path / "secrets" / "token.txt") == PathVerdict.DENIED


def test_traversal_does_not_escape_allowlist(tmp_path):
    """`workspace/../secrets/x` must resolve BEFORE matching -> DENIED."""
    p = policy(tmp_path)
    sneaky = tmp_path / "workspace" / ".." / "secrets" / "token.txt"
    assert p.check(sneaky) == PathVerdict.DENIED


def test_symlink_into_denied_dir_is_denied(tmp_path):
    """A symlink inside the workspace pointing at a denied dir is denied."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "token.txt").write_text("s3cret")
    link = tmp_path / "workspace" / "innocent.txt"
    link.symlink_to(tmp_path / "secrets" / "token.txt")
    p = policy(tmp_path)
    assert p.check(link) == PathVerdict.DENIED


def test_sensitive_filename_patterns_denied_anywhere(tmp_path):
    p = policy(tmp_path)
    for name in ["server.key", "cert.pem", "id_rsa", ".env", "vault.kdbx", "my-wallet.dat"]:
        assert p.check(tmp_path / "workspace" / name) == PathVerdict.DENIED, name


def test_default_denylist_covers_ssh():
    assert PathPolicy().check("~/.ssh/id_rsa") == PathVerdict.DENIED
    assert PathPolicy().check("~/Downloads/../.ssh/id_rsa") == PathVerdict.DENIED
