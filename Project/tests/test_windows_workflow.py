"""The manual Windows packaging workflow, checked as text (no YAML parser needed).

It must be manual-only, read-only, Windows, Python 3.12; lint and the complete
test suite must pass before anything is built; and the only things it uploads
are one zip of the whole dist/MAESTRO folder and that zip's SHA-256 manifest.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "windows-package.yml"


@pytest.fixture(scope="module")
def wf() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """The workflow without comment lines (so comments cannot satisfy or fail a check)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _block(text: str, key: str) -> str:
    """The lines of a top-level YAML block, e.g. `on:` up to the next top-level key."""
    m = re.search(rf"^{key}:\n((?:[ \t]+.*\n|\n)*)", text, re.M)
    return m.group(1) if m else ""


def _step_order(text: str) -> list[str]:
    return re.findall(r"^\s*- name: (.+)$", text, re.M)


def test_workflow_is_manual_only(wf):
    code = _code(wf)
    on = _block(code, "on")
    assert on.strip() == "workflow_dispatch:"
    for trigger in ("push", "pull_request", "pull_request_target", "schedule", "release",
                    "workflow_run", "repository_dispatch", "tags"):
        assert not re.search(rf"^\s*{trigger}\s*:", code, re.M), trigger


def test_permissions_are_read_only(wf):
    code = _code(wf)
    assert _block(code, "permissions").strip() == "contents: read"
    assert "write" not in code.lower().replace("overwrite", "")
    assert "persist-credentials: false" in code


def test_concurrency_and_timeout(wf):
    code = _code(wf)
    assert "group: windows-package-${{ github.ref }}" in code
    assert re.search(r"timeout-minutes: (\d+)", code) and \
        0 < int(re.search(r"timeout-minutes: (\d+)", code).group(1)) <= 60


def test_windows_python_312_from_the_project_folder(wf):
    code = _code(wf)
    assert "runs-on: windows-latest" in code
    assert 'python-version: "3.12"' in code
    assert "working-directory: Project" in code
    assert "cache: pip" in code and "cache-dependency-path: Project/pyproject.toml" in code
    assert 'pip install -e ".[desktop,google,voice,nlp,dev,packaging]"' in code


def test_lint_and_complete_tests_run_before_packaging(wf):
    code = _code(wf)
    steps = _step_order(code)
    ruff = steps.index("Ruff")
    tests = steps.index("Tests (complete suite)")
    build = steps.index("Build MAESTRO.exe")
    assert ruff < build and tests < build
    assert "run: python -m ruff check" in code
    assert re.search(r"run: python -m pytest\s*$", code, re.M)       # the whole suite, no -k / path


def test_build_and_non_interactive_validation_use_the_checked_in_scripts(wf):
    code = _code(wf)
    assert "run: ./scripts/build_windows.ps1" in code
    assert "run: ./scripts/test_windows_package.ps1 -SkipLaunch" in code
    steps = _step_order(code)
    assert steps.index("Build MAESTRO.exe") < steps.index("Validate the bundle (non-interactive)") \
        < steps.index("Zip the one-folder app and hash it")


def test_the_whole_folder_is_zipped_and_hashed(wf):
    code = _code(wf)
    assert 'Compress-Archive -Path "dist\\MAESTRO"' in code             # the folder, not the exe
    assert "Get-FileHash" in code and "-Algorithm SHA256" in code
    assert "MAESTRO-windows-portable.zip.sha256" in code
    assert "RUNNER_TEMP" in code                                      # outside the checkout


def test_only_the_zip_and_manifest_are_uploaded(wf):
    code = _code(wf)
    assert code.count("uses: actions/upload-artifact@") == 1
    upload = code.split("uses: actions/upload-artifact@", 1)[1]
    assert "name: MAESTRO-windows-portable" in upload
    block = re.search(r"path: \|\n((?:\s{12}.+\n)+)", upload).group(1)
    paths = [line.strip() for line in block.splitlines() if line.strip()]
    assert paths == ["${{ runner.temp }}/package/MAESTRO-windows-portable.zip",
                     "${{ runner.temp }}/package/MAESTRO-windows-portable.zip.sha256"]
    days = int(re.search(r"retention-days: (\d+)", upload).group(1))
    assert 1 <= days <= 14
    for never in ("build/", "dist/MAESTRO/MAESTRO.exe", ".pytest_cache", "*.db", "*.log"):
        assert never not in upload


def test_no_secrets_credentials_or_publishing(wf):
    code = _code(wf).lower()
    for forbidden in ("secrets.", "github_token", "client_secret", "token.json", ".pfx",
                      "signtool", "certificate", "gh release", "softprops/action-gh-release",
                      "actions/create-release", "upload-release-asset", "git push"):
        assert forbidden not in code, forbidden


def test_workflow_file_is_tracked_by_git_rules():
    """The workflow must not be git-ignored (it is source, not output)."""
    r = subprocess.run(["git", "check-ignore", "-q", str(WORKFLOW)], cwd=REPO,
                       capture_output=True, text=True)
    if r.returncode == 128:
        pytest.skip("not a git checkout")
    assert r.returncode == 1
