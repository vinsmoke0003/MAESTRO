<#
.SYNOPSIS
    Run MAESTRO from PowerShell without activating the venv.

.DESCRIPTION
    Thin launcher: every argument is passed straight to `python -m maestro.cli`
    using the project's .venv interpreter, so you never need `Activate.ps1`.

.EXAMPLE
    .\scripts\run.ps1 demo
    .\scripts\run.ps1 ask "move the pdfs from Downloads to Documents/Invoices"
    .\scripts\run.ps1 plan "organise Downloads by file type"
    .\scripts\run.ps1 undo
    .\scripts\run.ps1 ui              # local web interface at http://127.0.0.1:8765
    .\scripts\run.ps1 doctor
    .\scripts\run.ps1 audit --tail 10
#>
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venvPy = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "No .venv found. Run .\scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}

Set-Location $root
& $venvPy -m maestro.cli @args
exit $LASTEXITCODE
