# climaterisk Windows bundle launcher — started by "Start climaterisk.bat".
#
# Layout (next to this file): app\ (the program), ui\ (built web UI), env\ (one Python env
# with CLIMADA and the backend's dependencies). Starts the backend from env\python.exe, which
# serves the UI itself and spawns the CLIMADA worker from the same env — no uv, Node or
# conda on this machine. Closing this window stops the server.
#
# Settings: edit app\.env (copied from app\.env.example on first run). CLIMATERISK_BACKEND_PORT
# changes the port; CLIMATERISK_OFFLINE=1 is the closed-network mode (see README-WINDOWS.md).
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$EnvDir = Join-Path $Root "env"
$App = Join-Path $Root "app"
$Ui = Join-Path $Root "ui"
$Python = Join-Path $EnvDir "python.exe"

function Fail($msg) {
    Write-Host "!! $msg" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

if ($Root.Length -gt 60) {
    Write-Host ("!  This folder's path is long ({0} characters). Windows limits paths to 260 and the " -f $Root.Length) -ForegroundColor Yellow
    Write-Host "   Python environment is deep; if anything fails to start, move the folder to e.g. C:\climaterisk" -ForegroundColor Yellow
}
if (-not (Test-Path $Python)) { Fail "env\python.exe is missing — the bundle is incomplete; unzip it again" }
if (-not (Test-Path (Join-Path $Ui "index.html"))) { Fail "ui\index.html is missing — the bundle is incomplete" }
if (-not (Test-Path (Join-Path $App "src\climaterisk\api\main.py"))) { Fail "app\ is missing — the bundle is incomplete" }

# conda-pack relocation fix-up: once per location (paths inside env\ are rewritten).
$Marker = Join-Path $EnvDir ".unpacked-at"
if (-not (Test-Path $Marker) -or ((Get-Content $Marker -Raw).Trim() -ne $EnvDir)) {
    Write-Host "> first start in this folder: fixing up the Python environment (conda-unpack)…"
    $Unpack = Join-Path $EnvDir "Scripts\conda-unpack.exe"
    if (Test-Path $Unpack) { & $Unpack; if ($LASTEXITCODE -ne 0) { Fail "conda-unpack failed" } }
    Set-Content -Path $Marker -Value $EnvDir -Encoding ascii
}

# app\.env from the example on first run; then load it.
$EnvFile = Join-Path $App ".env"
if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $App ".env.example") $EnvFile }
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $val = $Matches[2].Trim().Trim('"').Trim("'")
        if ($val -ne "") { [Environment]::SetEnvironmentVariable($Matches[1], $val, "Process") }
    }
}

# Bundle-specific settings: both processes run from env\, the UI is the built one.
$env:CLIMATERISK_WORKER_PYTHON = $Python
$env:CLIMATERISK_UI_DIR = $Ui
$env:PYTHONPATH = (Join-Path $App "src")      # the backend package, un-installed (repo root = app\)
$env:PYTHONUTF8 = "1"
$env:MPLBACKEND = "Agg"
if (-not $env:CLIMATERISK_BACKEND_PORT) { $env:CLIMATERISK_BACKEND_PORT = "8099" }
$Port = $env:CLIMATERISK_BACKEND_PORT
$Url = "http://127.0.0.1:$Port/"

Set-Location $App
Write-Host "> stopping a previous climaterisk on port $Port, if any"
Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 300

Write-Host "> starting climaterisk → $Url"
$proc = Start-Process -FilePath $Python -PassThru -NoNewWindow -ArgumentList @(
    "-m", "uvicorn", "climaterisk.api.main:app", "--host", "127.0.0.1", "--port", $Port
)
try {
    $ok = $false
    for ($i = 0; $i -lt 120; $i++) {
        if ($proc.HasExited) { Fail "the server stopped right after starting — see the messages above" }
        try {
            Invoke-WebRequest -Uri "${Url}api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $ok = $true; break
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ok) { Fail "the server did not answer within 60 s" }
    Write-Host "> open $Url   (close this window to stop)"
    Start-Process $Url
    Wait-Process -Id $proc.Id
} finally {
    if ($proc -and -not $proc.HasExited) { & taskkill /PID $proc.Id /T /F 2>$null | Out-Null }
}
