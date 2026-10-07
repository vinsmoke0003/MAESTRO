<#
.SYNOPSIS
  Build the portable one-folder Windows app: dist\MAESTRO\MAESTRO.exe.

.DESCRIPTION
  Uses the ACTIVE Python environment (activate the venv first) and the checked-in
  spec, packaging\windows\maestro.spec. Install the build dependencies first:

      pip install -e ".[desktop,google,voice,nlp,packaging]"

  It deletes only its own two output folders, build\pyinstaller and dist\MAESTRO,
  inside this Project folder, and refuses to delete anything else.
#>

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "build_windows.ps1 must run on Windows: a Windows .exe has to be built on Windows."
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Spec        = Join-Path $ProjectRoot "packaging\windows\maestro.spec"
$BuildDir    = Join-Path $ProjectRoot "build\pyinstaller"
$DistRoot    = Join-Path $ProjectRoot "dist"
$DistDir     = Join-Path $DistRoot "MAESTRO"
$Exe         = Join-Path $DistDir "MAESTRO.exe"

function Remove-BuildDir([string] $Path) {
    # Only MAESTRO's two packaging output folders, and only inside this project.
    $allowed = @($BuildDir, $DistDir)
    if ($allowed -notcontains $Path) { throw "Refusing to delete unexpected path: $Path" }
    if (-not $Path.StartsWith($ProjectRoot + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to delete a path outside the project: $Path"
    }
    if (Test-Path -LiteralPath $Path) {
        Write-Host "Cleaning $Path"
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $Spec)) { throw "Spec not found: $Spec" }

$python = (Get-Command python -ErrorAction Stop).Source
Write-Host "Python: $python"
& python -c "import sys; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "python did not run" }
& python -c "import PyInstaller, webview"
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller or pywebview is missing: pip install -e ".[desktop,google,voice,nlp,packaging]"'
}

Remove-BuildDir $BuildDir
Remove-BuildDir $DistDir

Push-Location $ProjectRoot
try {
    # No --clean: it would also wipe PyInstaller's shared cache outside this project.
    # A fresh build is guaranteed by removing build\pyinstaller and dist\MAESTRO above.
    & python -m PyInstaller --noconfirm `
        --workpath $BuildDir --distpath $DistRoot $Spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit code $LASTEXITCODE)" }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $Exe)) { throw "Build finished but $Exe was not created" }
Write-Host ""
Write-Host "Built: $Exe"
