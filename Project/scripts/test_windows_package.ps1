<#
.SYNOPSIS
  Validate dist\MAESTRO\MAESTRO.exe on Windows.

.DESCRIPTION
  Static checks (always):
    - MAESTRO.exe exists and is a GUI-subsystem program (no console window);
    - ui.html and the intent classifier are in the bundle;
    - no client secret, token, database, .env, log or model file is in the bundle.

  Launch checks (skipped with -SkipLaunch):
    - the exe starts and its native window "MAESTRO" appears;
    - its local server listens on 127.0.0.1 only, and serves the page;
    - startup did not create a Google token or start the microphone;
    - closing the window ends the process and leaves no listening port.

  Still to confirm by eye (printed at the end): the window looks right, no console
  window flashes, and Windows shows no microphone-in-use indicator.
#>
param(
    [string] $Exe = (Join-Path (Join-Path $PSScriptRoot "..") "dist\MAESTRO\MAESTRO.exe"),
    [switch] $SkipLaunch,
    [int] $StartTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "test_windows_package.ps1 must run on Windows."
}

$failures = New-Object System.Collections.Generic.List[string]
function Check([bool] $ok, [string] $what) {
    if ($ok) { Write-Host "  [ok]   $what" } else { Write-Host "  [FAIL] $what"; $failures.Add($what) }
}

Write-Host "Static checks"
Check (Test-Path -LiteralPath $Exe) "MAESTRO.exe exists"
if (-not (Test-Path -LiteralPath $Exe)) { throw "Build first: scripts\build_windows.ps1" }
$Exe = (Resolve-Path -LiteralPath $Exe).Path
$Bundle = Split-Path -Parent $Exe

# PE optional header: Subsystem 2 = Windows GUI (no console), 3 = console.
$bytes = [System.IO.File]::ReadAllBytes($Exe)
$pe = [System.BitConverter]::ToInt32($bytes, 0x3C)
$subsystem = [System.BitConverter]::ToUInt16($bytes, $pe + 0x5C)
Check ($subsystem -eq 2) "windowed executable (PE subsystem $subsystem, expected 2)"

$ui = Get-ChildItem -LiteralPath $Bundle -Recurse -Filter "ui.html" |
      Where-Object { $_.Directory.Name -eq "maestro" }
Check ($null -ne $ui) "maestro\ui.html is bundled"
$model = Get-ChildItem -LiteralPath $Bundle -Recurse -Filter "intent_clf.joblib"
Check ($null -ne $model) "intent classifier is bundled"

$forbidden = @("client_secret*.json", "token.json", "token_*.json", "credentials*.json",
               "*.db", "*.sqlite", "*.sqlite3", "*.log", ".env", "*.key", "model.bin",
               "*.gguf", "*.safetensors")
$found = @()
foreach ($pattern in $forbidden) {
    $found += @(Get-ChildItem -LiteralPath $Bundle -Recurse -Force -File -Filter $pattern)
}
Check ($found.Count -eq 0) "no credentials, tokens, databases, logs or models in the bundle"
foreach ($f in $found) { Write-Host "         found: $($f.FullName)" }

if ($SkipLaunch) {
    Write-Host "Launch checks skipped (-SkipLaunch)."
} else {
    Write-Host "Launch checks"
    $token = Join-Path $env:USERPROFILE ".maestro\google\token.json"
    $tokenBefore = Test-Path -LiteralPath $token

    $proc = Start-Process -FilePath $Exe -PassThru
    try {
        $deadline = (Get-Date).AddSeconds($StartTimeoutSeconds)
        $listen = @()
        while ((Get-Date) -lt $deadline -and $listen.Count -eq 0 -and -not $proc.HasExited) {
            Start-Sleep -Milliseconds 500
            $listen = @(Get-NetTCPConnection -OwningProcess $proc.Id -State Listen `
                        -ErrorAction SilentlyContinue)
        }
        Check (-not $proc.HasExited) "MAESTRO.exe is running"
        Check ($listen.Count -gt 0) "local server is listening"
        $addresses = @($listen | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique)
        Check (($addresses.Count -eq 1) -and ($addresses[0] -eq "127.0.0.1")) `
              "listens on 127.0.0.1 only (got: $($addresses -join ', '))"
        $port = if ($listen.Count -gt 0) { $listen[0].LocalPort } else { 0 }

        if ($port) {
            $page = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/"
            Check ($page.StatusCode -eq 200 -and $page.Content -match "MAESTRO") "serves the MAESTRO page"
            $voice = Invoke-RestMethod "http://127.0.0.1:$port/api/voice/status"
            Check (@("idle", "unavailable") -contains $voice.state) `
                  "microphone not started at launch (voice state: $($voice.state))"
        }

        $window = $false
        while ((Get-Date) -lt $deadline -and -not $window -and -not $proc.HasExited) {
            $proc.Refresh()
            $window = ($proc.MainWindowHandle -ne [IntPtr]::Zero -and $proc.MainWindowTitle -eq "MAESTRO")
            if (-not $window) { Start-Sleep -Milliseconds 500 }
        }
        Check $window "native window 'MAESTRO' is open"
        Check ((Test-Path -LiteralPath $token) -eq $tokenBefore) "no Google token was created"

        # Close the window the way a user does (WM_CLOSE), then wait for the process.
        [void] $proc.CloseMainWindow()
        $exited = $proc.WaitForExit(20000)
        Check $exited "closing the window ends the process"
        if ($port) {
            Start-Sleep -Milliseconds 500
            $left = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
            Check ($left.Count -eq 0) "no listening port left behind (port $port)"
        }
    } finally {
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    }
}

Write-Host ""
Write-Host "Confirm by eye (not automated):"
Write-Host "  - the MAESTRO window looks right and the pages work;"
Write-Host "  - no console window flashed at startup;"
Write-Host "  - Windows showed no microphone-in-use indicator until you pressed Start recording."

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "$($failures.Count) check(s) failed."
    exit 1
}
Write-Host ""
Write-Host "All automated checks passed."
