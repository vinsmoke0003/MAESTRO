<#
.SYNOPSIS
    Set up MAESTRO on Windows, end to end, and tell you what will work.

.DESCRIPTION
    Windows-friendly equivalent of `make install`, `make dataset`, `make train-intent`
    and `maestro doctor`. Idempotent: run it again after pulling changes.

    What it does, in order:
      1. creates .venv with the newest Python 3.10+ it can find
      2. installs MAESTRO with the nlp + dev extras (pydantic, send2trash, sklearn, pytest)
      3. optionally installs the browser and system extras (-WithBrowser, -WithSystem)
      4. builds the DeskPlan dataset and validates its invariants
      5. trains the intent classifier (seconds; no GPU)
      6. runs `maestro doctor` — a real readiness probe, feature by feature

    Nothing here needs an API key, a model download, or the network beyond pip.

.PARAMETER WithBrowser
    Also install Playwright and download headless Chromium (~150 MB). Needed
    only for browser.* verbs.

.PARAMETER WithSystem
    Also install psutil (memory/battery/cpu metrics) and pycaw (volume control).

.PARAMETER SkipTests
    Do not run the test suite at the end.

.EXAMPLE
    .\scripts\setup.ps1
    .\scripts\setup.ps1 -WithBrowser -WithSystem
    .\scripts\run.ps1 demo
#>
[CmdletBinding()]
param(
    [switch]$WithBrowser,
    [switch]$WithSystem,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }

# --- 1. interpreter -----------------------------------------------------------
Step "Locating Python 3.10+"
$py = $null
foreach ($candidate in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python")) {
    try {
        $v = & cmd /c "$candidate -c `"import sys;print(sys.version_info[:2]>=(3,10))`"" 2>$null
        if ($v -eq "True") { $py = $candidate; break }
    } catch { }
}
if (-not $py) {
    Write-Host "No Python 3.10+ found. Install from https://www.python.org/downloads/ and re-run." -ForegroundColor Red
    exit 1
}
Write-Host "using: $py"

if (-not (Test-Path ".venv")) {
    Step "Creating .venv"
    & cmd /c "$py -m venv .venv"
}
$venvPy = Join-Path $root ".venv\Scripts\python.exe"

# --- 2. core install ------------------------------------------------------------
Step "Installing MAESTRO (core + nlp + dev)"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -e ".[nlp,dev]"

# --- 3. optional extras ---------------------------------------------------------
if ($WithBrowser) {
    Step "Installing Playwright + Chromium (browser verbs)"
    & $venvPy -m pip install --quiet playwright
    & $venvPy -m playwright install chromium
}
if ($WithSystem) {
    Step "Installing psutil + pycaw (system metrics, volume)"
    & $venvPy -m pip install --quiet psutil pycaw comtypes
}

# --- 4. dataset --------------------------------------------------------------------
Step "Building the DeskPlan dataset"
& $venvPy data\build_dataset.py
& $venvPy data\validate_dataset.py
if ($LASTEXITCODE -ne 0) { Write-Host "dataset validation FAILED" -ForegroundColor Red; exit 1 }

# --- 5. benchmark + intent model -------------------------------------------------
Step "Building the benchmark suites"
& $venvPy eval\build_benchmark.py

Step "Training the intent classifier"
& $venvPy training\train_intent.py

# --- 6. tests -------------------------------------------------------------------------
if (-not $SkipTests) {
    Step "Running the test suite"
    & $venvPy -m pytest -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { Write-Host "tests FAILED" -ForegroundColor Red; exit 1 }
}

# --- 7. readiness ---------------------------------------------------------------------
Step "Readiness check (maestro doctor)"
& $venvPy -m maestro.cli doctor

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Try:   .\scripts\run.ps1 demo"
Write-Host "         .\scripts\run.ps1 ask ""move the pdfs from Downloads to Documents/Invoices"""
Write-Host "         .\scripts\run.ps1 ui        (local web interface)"
Write-Host "  Anything `maestro doctor` marked MISSING above should not be part of a live demo on this machine."
