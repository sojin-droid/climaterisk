# climaterisk launcher (Windows) — starts the orchestration backend (FastAPI/uvicorn)
# and the frontend (Vite), waits for the backend to be healthy, then opens the app.
# The CLIMADA worker (Phase 2) runs on demand as a separate conda subprocess;
# this script only checks whether its env is present.
#
# Double-click run.bat, or run:  powershell -ExecutionPolicy Bypass -File run.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Load .env if present (export each KEY=VALUE; skip comments/blank lines).
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $val = $Matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($Matches[1], $val, "Process")
        }
    }
}

$BackendHost  = if ($env:CLIMATERISK_BACKEND_HOST)  { $env:CLIMATERISK_BACKEND_HOST }  else { "127.0.0.1" }
$BackendPort  = if ($env:CLIMATERISK_BACKEND_PORT)  { $env:CLIMATERISK_BACKEND_PORT }  else { "8099" }
$FrontendPort = if ($env:CLIMATERISK_FRONTEND_PORT) { $env:CLIMATERISK_FRONTEND_PORT } else { "5174" }
$FrontendDir  = "frontend\climaterisk"
# Project-local CLIMADA conda (prefix) env — never a global/named env.
$WorkerEnvDir = if ($env:CLIMATERISK_WORKER_ENV_DIR) { $env:CLIMATERISK_WORKER_ENV_DIR } else { ".climada-env" }
$env:CLIMATERISK_BACKEND_PORT = $BackendPort
$env:CLIMATERISK_FRONTEND_PORT = $FrontendPort

Write-Host "> stopping any previous climaterisk servers..."
foreach ($port in @($BackendPort, $FrontendPort)) {
    Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "uvicorn climaterisk\.api\.main:app" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "!! 'uv' not found. Install it first:  winget install astral-sh.uv" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "!! 'npm' not found. Install Node.js first:  winget install OpenJS.NodeJS.LTS" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "> installing backend deps (uv sync)..."
    uv sync --all-extras
}
if (-not (Test-Path "$FrontendDir\node_modules")) {
    Write-Host "> installing frontend deps (npm install)..."
    Push-Location $FrontendDir
    npm install
    Pop-Location
}

# Soft check: is the project-local CLIMADA worker env present? (Non-fatal — only Phase 2.)
if (Test-Path "$WorkerEnvDir\python.exe") {
    Write-Host "> CLIMADA worker env '$WorkerEnvDir' OK"
} else {
    Write-Host "> CLIMADA worker env '$WorkerEnvDir' not found - physical-risk runs (Phase 2) unavailable."
    Write-Host "  build it: conda env create -f worker/climaterisk_worker/env_climada.yml --prefix ./$WorkerEnvDir"
}

$procs = @()
try {
    Write-Host "> backend  -> http://${BackendHost}:${BackendPort}"
    # --reload-dir src: watch ONLY backend source. Watching the repo root makes uvicorn
    # reload on every SQLite-WAL write under data/ (one per status poll), which orphans
    # in-flight worker subprocesses and leaves runs stuck polling "running" forever.
    $procs += Start-Process -FilePath "uv" -PassThru -NoNewWindow -ArgumentList @(
        "run", "uvicorn", "climaterisk.api.main:app",
        "--host", $BackendHost, "--port", $BackendPort, "--reload", "--reload-dir", "src"
    )

    Write-Host "> frontend -> http://localhost:${FrontendPort}"
    $procs += Start-Process -FilePath "npm.cmd" -PassThru -NoNewWindow `
        -WorkingDirectory (Join-Path $PSScriptRoot $FrontendDir) `
        -ArgumentList @("run", "dev", "--", "--port", $FrontendPort)

    Write-Host -NoNewline "> waiting for backend"
    for ($i = 0; $i -lt 60; $i++) {
        try {
            Invoke-WebRequest -Uri "http://${BackendHost}:${BackendPort}/api/health" `
                -UseBasicParsing -TimeoutSec 2 | Out-Null
            Write-Host " OK"
            break
        } catch {
            Write-Host -NoNewline "."
            Start-Sleep -Milliseconds 500
        }
    }

    $url = "http://localhost:${FrontendPort}"
    Start-Sleep -Seconds 2
    Start-Process $url
    Write-Host "> open $url  (Ctrl-C to stop)"

    Wait-Process -Id ($procs | ForEach-Object Id)
} finally {
    Write-Host ""
    Write-Host "> shutting down..."
    foreach ($p in $procs) {
        if ($p -and -not $p.HasExited) {
            # Kill the whole tree: uvicorn --reload and npm both spawn child processes.
            & taskkill /PID $p.Id /T /F 2>$null | Out-Null
        }
    }
}
