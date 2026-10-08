# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable Windows build: dist/MAESTRO/MAESTRO.exe.

Build it on Windows with scripts/build_windows.ps1 (never by hand-editing dist/).

* one-folder (onedir): EXE(exclude_binaries=True) + COLLECT. No one-file mode.
* windowed: console=False. The entry point is maestro/desktop_entry.py, which
  only calls maestro.desktop.run(): the same local server (127.0.0.1, free
  port), Workspace, pipeline and consent workflow as `maestro desktop`.
* MAESTRO's own data files come from bundle_rules.bundle_datas() (ui.html and
  the trained intent classifier). Nothing from the user's home is read, and
  bundle_rules.check_datas() refuses tokens, client secrets, databases, .env
  files, logs and downloaded models before anything is written.
* The Whisper speech model is NOT bundled: it is downloaded on first use into
  the user's cache, as with `maestro voice`. Runtime data goes to the normal
  MAESTRO locations (~/.maestro and ~/maestro_workspace), never into the bundle.
"""

import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()          # noqa: F821 - provided by PyInstaller
ROOT = SPEC_DIR.parents[1]                    # the Project folder
sys.path.insert(0, str(SPEC_DIR))
import bundle_rules  # noqa: E402


def installed(module):
    return importlib.util.find_spec(module) is not None


# ---- what to include -----------------------------------------------------------

datas = bundle_rules.bundle_datas(ROOT)
binaries = []
hiddenimports = collect_submodules("maestro")   # executors and platform backends are
                                                # imported dynamically, so list them all

if installed("webview"):                         # desktop window (required)
    datas += collect_data_files("webview")
    hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium",
                      "clr"]
else:
    raise SystemExit('pywebview is not installed: pip install -e ".[desktop]"')

if installed("faster_whisper"):                  # voice: package assets only, no model
    datas += collect_data_files("faster_whisper")
    binaries += collect_dynamic_libs("ctranslate2")
    hiddenimports += ["sounddevice", "_sounddevice_data"]

if installed("googleapiclient"):                 # Gmail / Drive / Calendar
    datas += collect_data_files("googleapiclient")
    hiddenimports += ["google_auth_oauthlib.flow", "google.oauth2.credentials",
                      "google.auth.transport.requests", "googleapiclient.discovery"]

if installed("sklearn"):                         # the trained intent classifier is a pickle
    hiddenimports += collect_submodules("sklearn")

# ---- what to keep out ------------------------------------------------------------

excludes = [
    "tests", "pytest", "_pytest", "ruff",       # development only
    "eval", "training", "data",                 # MAESTRO's research code, not the app
    "playwright", "torch", "transformers", "chromadb", "spacy", "tensorflow",
    "PyInstaller",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "maestro" / "desktop_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    excludes=excludes,
    noarchive=False,
)

# Last line of defence: inspect everything about to be bundled.
bundle_rules.check_datas(a.datas, home=Path.home())
bundle_rules.check_datas(a.binaries, home=Path.home())

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir: binaries go to COLLECT, not into the exe
    name="MAESTRO",
    debug=False,
    strip=False,
    upx=False,
    console=False,              # windowed: no console window
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MAESTRO",             # dist/MAESTRO/MAESTRO.exe
)
