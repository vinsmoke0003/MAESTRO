"""The Windows packaging files, checked without PyInstaller, Windows or a GUI.

The real build runs on Windows (scripts/build_windows.ps1); these tests pin the
promises its inputs make: the exe only launches the existing desktop shell, the
spec is windowed one-folder and bundles the page, credentials and user data can
never be bundled, the build script deletes only its own output, and generated
output stays out of git.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from maestro import desktop_entry

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "windows" / "maestro.spec"
BUILD_PS1 = ROOT / "scripts" / "build_windows.ps1"
TEST_PS1 = ROOT / "scripts" / "test_windows_package.ps1"


def _rules():
    spec = importlib.util.spec_from_file_location(
        "bundle_rules", ROOT / "packaging" / "windows" / "bundle_rules.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the entry point ----------------------------------------------------------


def test_entry_runs_the_existing_desktop_shell(monkeypatch):
    from maestro import desktop

    calls = []
    monkeypatch.setattr(desktop, "run", lambda **kw: calls.append(kw) or 0)
    shown = []
    monkeypatch.setattr(desktop_entry, "_show_error", shown.append)
    assert desktop_entry.main() == 0
    assert calls == [{}] and shown == []


def test_entry_reports_failure_without_details(monkeypatch):
    from maestro import desktop

    def boom(**kw):
        raise RuntimeError(r"C:\Users\me\.maestro\google\token.json refresh_token=abc")

    monkeypatch.setattr(desktop, "run", boom)
    shown = []
    monkeypatch.setattr(desktop_entry, "_show_error", shown.append)
    assert desktop_entry.main() == 1
    assert shown == [desktop_entry.FAILED]
    assert "token" not in shown[0] and "Users" not in shown[0]


def test_entry_passes_on_a_nonzero_status(monkeypatch):
    from maestro import desktop

    monkeypatch.setattr(desktop, "run", lambda **kw: 1)          # e.g. port unavailable
    shown = []
    monkeypatch.setattr(desktop_entry, "_show_error", shown.append)
    assert desktop_entry.main() == 1 and shown == [desktop_entry.FAILED]


def test_entry_gives_a_windowed_process_usable_streams(monkeypatch):
    from maestro import desktop

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(desktop, "run", lambda **kw: print("progress", file=sys.stderr) or 0)
    assert desktop_entry.main() == 0                              # no AttributeError
    assert sys.stdout is not None and sys.stderr is not None
    sys.stdout.close()
    sys.stderr.close()


def test_entry_creates_no_second_server_or_pipeline():
    src = (ROOT / "maestro" / "desktop_entry.py").read_text(encoding="utf-8")
    for forbidden in ("Workspace(", "MaestroPipeline(", "LocalServer(",
                      "ThreadingHTTPServer", "make_handler", "webview"):
        assert forbidden not in src
    assert "desktop.run()" in src


def test_only_ui_py_builds_workspaces_and_servers():
    hits = {}
    for path in (ROOT / "maestro").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("Workspace(", "ThreadingHTTPServer(("):
            if re.search(r"(?<![\w.])" + re.escape(needle), text):
                hits.setdefault(needle, set()).add(path.name)
    assert hits == {"Workspace(": {"ui.py"}, "ThreadingHTTPServer((": {"ui.py"}}


# ---- the spec -------------------------------------------------------------------


def test_spec_is_windowed_onedir_and_named_maestro():
    spec = SPEC.read_text(encoding="utf-8")
    assert re.search(r"console=False", spec) and "console=True" not in spec
    assert "exclude_binaries=True" in spec and "COLLECT(" in spec        # onedir
    assert "onefile" not in spec.lower()
    assert spec.count('name="MAESTRO"') == 2                             # exe + folder
    assert "desktop_entry.py" in spec
    assert "debug=False" in spec


def test_spec_bundles_the_page_and_checks_everything_it_bundles():
    spec = SPEC.read_text(encoding="utf-8")
    assert "bundle_rules.bundle_datas(ROOT)" in spec
    assert "bundle_rules.check_datas(a.datas" in spec and "bundle_rules.check_datas(a.binaries" in spec
    assert 'collect_submodules("maestro")' in spec
    assert "webview.platforms.edgechromium" in spec and "webview.platforms.winforms" in spec
    for excluded in ('"tests"', '"pytest"', '"eval"', '"training"'):
        assert excluded in spec


def test_bundle_datas_are_the_page_and_the_intent_model():
    datas = _rules().bundle_datas(ROOT)
    assert datas == [(str(ROOT / "maestro" / "ui.html"), "maestro"),
                     (str(ROOT / "models" / "intent" / "intent_clf.joblib"), "models/intent")]
    for src, _ in datas:
        assert Path(src).is_file()
    # The bundle folders mirror how the running code finds these files.
    from maestro import config, ui
    assert ui.PAGE.parent.name == "maestro"
    assert config.settings().intent_model.parts[-3:] == ("models", "intent", "intent_clf.joblib")


@pytest.mark.parametrize("path", [
    "client_secret.json", "client_secret_123.apps.json", "token.json", "token_backup.json",
    "credentials.json", "audit.db", "episodes.sqlite3", "maestro.log", ".env", ".env.local",
    "server.key", "model.bin", "llama.gguf", "weights.safetensors",
])
def test_credentials_tokens_databases_and_models_are_refused(path):
    rules = _rules()
    assert rules.forbidden_reason(f"/somewhere/{path}") is not None
    with pytest.raises(SystemExit):
        rules.check_datas([("x", f"/somewhere/{path}", "DATA")])


def test_user_state_and_model_caches_are_refused(tmp_path):
    rules = _rules()
    home = tmp_path
    for p in (home / ".maestro" / "google" / "notes.txt",
              home / "maestro_workspace" / "drafts" / "a.md",
              home / ".cache" / "huggingface" / "hub" / "config.json"):
        assert rules.forbidden_reason(str(p), home) is not None
    with pytest.raises(SystemExit):
        rules.check_datas([(str(home / ".maestro" / "anything.txt"), ".")], home)


def test_ordinary_package_data_is_allowed():
    rules = _rules()
    for p in (str(ROOT / "maestro" / "ui.html"),
              "/venv/lib/site-packages/certifi/cacert.pem",          # HTTPS needs it
              "/venv/lib/site-packages/webview/js/api.js",
              "/venv/lib/site-packages/faster_whisper/assets/silero_vad.onnx",
              "/venv/lib/site-packages/googleapiclient/discovery_cache/documents/drive.v3.json"):
        assert rules.forbidden_reason(p) is None, p
    rules.check_datas(rules.bundle_datas(ROOT))                      # does not raise


# ---- the scripts and git ----------------------------------------------------------


def test_build_script_is_strict_and_deletes_only_its_own_output():
    ps = BUILD_PS1.read_text(encoding="utf-8")
    assert '$ErrorActionPreference = "Stop"' in ps
    assert "Win32NT" in ps                                           # Windows only
    assert 'Join-Path $ProjectRoot "build\\pyinstaller"' in ps
    assert 'Join-Path $DistRoot "MAESTRO"' in ps
    assert 'Join-Path $ProjectRoot "packaging\\windows\\maestro.spec"' in ps
    removes = re.findall(r"Remove-Item[^\n]*", ps)
    assert removes == ["Remove-Item -LiteralPath $Path -Recurse -Force"]   # one, guarded
    assert "$allowed = @($BuildDir, $DistDir)" in ps
    calls = re.findall(r"^\s*Remove-BuildDir (\S+)", ps, re.M)
    assert calls == ["$BuildDir", "$DistDir"]
    assert "MAESTRO.exe" in ps and "Test-Path -LiteralPath $Exe" in ps
    assert "python -m PyInstaller" in ps
    # --clean would clear PyInstaller's shared cache outside the project: not allowed.
    executable = "\n".join(line for line in ps.split("#>", 1)[1].splitlines()
                           if not line.lstrip().startswith("#"))
    assert "--clean" not in executable
    # Uses the active environment: it never installs anything itself.
    code = ps.split("#>", 1)[1]                                      # after the help block
    assert re.search(r"^[ \t]*(&[ \t]*)?(python[ \t]+-m[ \t]+)?pip[ \t]+install", code,
                     re.M) is None


def test_validation_script_covers_the_required_checks():
    ps = TEST_PS1.read_text(encoding="utf-8")
    for needle in ("MAESTRO.exe exists", "ui.html", "127.0.0.1 only", "native window",
                   "closing the window ends the process", "no listening port left behind",
                   "PE subsystem", "no Google token was created", "microphone not started",
                   "Confirm by eye", "-SkipLaunch"):
        assert needle in ps, needle


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


@pytest.mark.skipif(_git("rev-parse", "--git-dir").returncode != 0, reason="not a git checkout")
def test_build_output_is_ignored_but_sources_are_not():
    for generated in ("dist/MAESTRO/MAESTRO.exe", "build/pyinstaller/warn-maestro.txt"):
        assert _git("check-ignore", "-q", generated).returncode == 0, generated
    for source in ("packaging/windows/maestro.spec", "packaging/windows/bundle_rules.py",
                   "scripts/build_windows.ps1", "scripts/test_windows_package.ps1",
                   "maestro/desktop_entry.py", "tests/test_packaging.py"):
        assert _git("check-ignore", "-q", source).returncode == 1, source


def test_packaging_dependencies_are_optional():
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    core = toml.split("[project.optional-dependencies]", 1)[0]
    assert "pyinstaller" not in core.lower() and "pywebview" not in core.lower()
    assert 'packaging = ["pyinstaller>=6"]' in toml
    assert 'desktop = ["pywebview>=5"]' in toml


def test_spec_is_valid_python():
    compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec")
