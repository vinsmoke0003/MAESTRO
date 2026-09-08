"""Windows application backend. No shell — `subprocess` with an argument list.

`start`-style shell invocation is deliberately avoided: it would reintroduce
string interpolation of an app name, which is exactly the class of bug the
closed verb registry exists to remove.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from maestro.executor.base import NotAvailable

# Friendly name -> (executable, process image name for taskkill)
KNOWN_APPS: dict[str, tuple[str, str]] = {
    "chrome": ("chrome.exe", "chrome.exe"),
    "google chrome": ("chrome.exe", "chrome.exe"),
    "edge": ("msedge.exe", "msedge.exe"),
    "microsoft edge": ("msedge.exe", "msedge.exe"),
    "firefox": ("firefox.exe", "firefox.exe"),
    "notepad": ("notepad.exe", "notepad.exe"),
    "explorer": ("explorer.exe", "explorer.exe"),
    "file explorer": ("explorer.exe", "explorer.exe"),
    "calculator": ("calc.exe", "CalculatorApp.exe"),
    "vs code": ("code.cmd", "Code.exe"),
    "vscode": ("code.cmd", "Code.exe"),
    "visual studio code": ("code.cmd", "Code.exe"),
    "terminal": ("wt.exe", "WindowsTerminal.exe"),
    "spotify": ("spotify.exe", "Spotify.exe"),
    "word": ("winword.exe", "WINWORD.EXE"),
    "excel": ("excel.exe", "EXCEL.EXE"),
    "powerpoint": ("powerpnt.exe", "POWERPNT.EXE"),
}


def _resolve(app_id: str) -> tuple[str, str]:
    key = app_id.strip().lower()
    if key in KNOWN_APPS:
        return KNOWN_APPS[key]
    # Allow a bare executable name that is actually on PATH.
    exe = key if key.endswith(".exe") else f"{key}.exe"
    if _find_executable(exe):
        return exe, exe
    raise NotAvailable(
        f"unknown application {app_id!r} on Windows — add it to KNOWN_APPS or install it"
    )


# Where Windows applications actually live. Almost nothing outside the system
# directory is on PATH — Chrome and Firefox are both installed under Program
# Files and neither is resolvable with `shutil.which`, so a PATH-only lookup
# reports "not installed" for software that is plainly installed.
_SEARCH_DIRS: tuple[str, ...] = (
    r"%ProgramFiles%",
    r"%ProgramFiles(x86)%",
    r"%LocalAppData%\Programs",
    r"%LocalAppData%",
    r"%AppData%",
)

# Vendor sub-paths under the directories above, for the apps people actually ask
# for by name.
_KNOWN_LOCATIONS: dict[str, tuple[str, ...]] = {
    "chrome.exe": (r"Google\Chrome\Application\chrome.exe",),
    "msedge.exe": (r"Microsoft\Edge\Application\msedge.exe",),
    "firefox.exe": (r"Mozilla Firefox\firefox.exe",),
    "code.cmd": (r"Microsoft VS Code\bin\code.cmd", r"Microsoft VS Code\Code.exe"),
    "spotify.exe": (r"Spotify\Spotify.exe",),
    "winword.exe": (r"Microsoft Office\root\Office16\WINWORD.EXE",),
    "excel.exe": (r"Microsoft Office\root\Office16\EXCEL.EXE",),
    "powerpnt.exe": (r"Microsoft Office\root\Office16\POWERPNT.EXE",),
    "wt.exe": (r"Microsoft\WindowsApps\wt.exe",),
}


def _find_executable(exe: str) -> str | None:
    """PATH first, then the standard install locations, then the registry."""
    found = shutil.which(exe)
    if found:
        return found

    for base in _SEARCH_DIRS:
        root = os.path.expandvars(base)
        if "%" in root or not os.path.isdir(root):
            continue
        for rel in _KNOWN_LOCATIONS.get(exe.lower(), ()):
            candidate = os.path.join(root, rel)
            if os.path.isfile(candidate):
                return candidate

    return _from_app_paths(exe)


def _from_app_paths(exe: str) -> str | None:
    """The registry key Windows itself uses to resolve `Run` dialog names.

    HKLM/HKCU ...\\App Paths\\<name>.exe is how "chrome" works in the Run box
    without being on PATH, so honouring it means MAESTRO finds whatever the user
    can already launch by name.
    """
    try:
        import winreg
    except ImportError:  # not Windows
        return None
    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key) as k:
                path, _ = winreg.QueryValueEx(k, "")
                path = os.path.expandvars(str(path).strip('"'))
                if os.path.isfile(path):
                    return path
        except OSError:
            continue
    return None


def launch(app_id: str) -> str:
    exe, _ = _resolve(app_id)
    path = _find_executable(exe)
    if path is None:
        raise NotAvailable(
            f"{exe} is not installed, or not in PATH, the standard Program Files "
            f"locations, or the App Paths registry"
        )
    subprocess.Popen([path], close_fds=True)  # noqa: S603 - argv list, no shell
    return f"launched {exe}"


def quit(app_id: str, force: bool = False) -> str:  # noqa: A001 - mirrors the verb name
    _, image = _resolve(app_id)
    cmd = ["taskkill", "/IM", image]
    if force:
        cmd.append("/F")
    proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    if proc.returncode != 0:
        raise NotAvailable(f"could not quit {app_id}: {proc.stdout.strip() or proc.stderr.strip()}")
    return f"quit {image}"
