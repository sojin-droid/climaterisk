# Build the Windows bundle: one zip a non-developer unzips and double-clicks.
#
# What ends up in the zip (climaterisk-win64-<version>.zip):
#   app\     this checkout's runtime files (src, worker, assets, scripts, docs, pyproject, ...)
#   ui\      the built single-page UI (vite build) - served by the backend, no Node at runtime
#   env\     one relocatable Python environment (conda-pack) holding CLIMADA *and* the
#            backend's dependencies; the start script points both the backend and the worker
#            at env\python.exe, so neither uv nor conda is needed on the target machine
#   Start climaterisk.bat, start.ps1, README-WINDOWS.md
#
# The EIOPA CLIMADA-App ships the same shape (frozen conda env + exe + short-path warning).
# Like theirs, the unzipped folder must sit in a SHORT path (e.g. C:\climaterisk): Windows
# caps paths at 260 characters and the env is deep.
#
# Usage (from the repo root, in PowerShell; needs conda/mamba, Node, and ~6 GB of scratch):
#   powershell -ExecutionPolicy Bypass -File scripts\make_windows_bundle.ps1 [-Env .climada-env] [-FromYml] [-Out build]
#
#   -Env     an existing CLIMADA env to clone (default .climada-env; fast, hard-links)
#   -FromYml build the packaging env fresh from worker\climaterisk_worker\env_climada.yml (~20 min)
#   -Out     scratch + output directory (default build\, git-ignored)
#   -Conda   path to conda.exe when it is not on PATH (CONDA_EXE is honoured)
#   -SkipZip leave build\bundle\ unzipped (for a smoke test)
#   -KeepPkgEnv keep build\pkg-env after packing (faster rebuilds; costs ~2 GB of disk)
param(
    [string]$Env = ".climada-env",
    [switch]$FromYml,
    [string]$Out = "build",
    [string]$Conda = "",
    [switch]$SkipZip,
    [switch]$KeepPkgEnv
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Repo

function Step($msg) { Write-Host "> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "!! $msg" -ForegroundColor Red; exit 1 }

# Disk: packaging env (~2.2 GB) + archive (~0.6 GB) + extracted env (~2.2 GB) + zip (~1 GB).
# The packaging env is deleted right after packing unless -KeepPkgEnv, so the peak is ~3 GB.
$OutDir = Join-Path $Repo $Out
$PkgEnv = Join-Path $OutDir "pkg-env"
$MinFreeGb = if (Test-Path (Join-Path $PkgEnv "python.exe")) { 3 } else { 6 }   # env already built -> no clone
$Drive = (Get-Item $Repo).PSDrive
$FreeGb = [math]::Round($Drive.Free / 1GB, 1)
if ($FreeGb -lt $MinFreeGb) { Fail "only $FreeGb GB free on $($Drive.Name): - the bundle build needs about $MinFreeGb GB of scratch space" }

# ---- tools ---------------------------------------------------------------------------
if (-not $Conda) {
    if ($env:CONDA_EXE -and (Test-Path $env:CONDA_EXE)) { $Conda = $env:CONDA_EXE }
    else {
        $c = Get-Command conda.exe -ErrorAction SilentlyContinue
        if ($c) { $Conda = $c.Source }
        else {
            foreach ($cand in @("$env:USERPROFILE\miniforge3\Scripts\conda.exe", "$env:USERPROFILE\miniconda3\Scripts\conda.exe", "$env:USERPROFILE\anaconda3\Scripts\conda.exe", "$env:ProgramData\miniconda3\Scripts\conda.exe")) {
                if (Test-Path $cand) { $Conda = $cand; break }
            }
        }
    }
}
if (-not $Conda -or -not (Test-Path $Conda)) { Fail "conda.exe not found - pass -Conda <path> (miniforge: winget install CondaForge.Miniforge3)" }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "npm not found - install Node.js LTS (winget install OpenJS.NodeJS.LTS); it is only needed to BUILD the bundle" }
$Tar = Join-Path $env:SystemRoot "System32\tar.exe"   # bsdtar: zip64, so >2 GB archives work
if (-not (Test-Path $Tar)) { Fail "System32\tar.exe (bsdtar) not found - Windows 10 1803+ required" }

# ---- version -------------------------------------------------------------------------
$Version = (Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$Git = ""
try { $Git = (git rev-parse --short HEAD 2>$null) } catch {}
$Label = if ($Git) { "$Version+$Git" } else { $Version }
Step "climaterisk $Label -> $Out\"

$Bundle = Join-Path $OutDir "bundle"
$Zip = Join-Path $OutDir "climaterisk-win64-$Version.zip"
New-Item -ItemType Directory -Force $OutDir | Out-Null
if (Test-Path $Bundle) { Remove-Item -Recurse -Force $Bundle }
New-Item -ItemType Directory -Force $Bundle | Out-Null

# ---- 1. the UI ------------------------------------------------------------------------
Step "1/6 building the UI (vite build)"
Push-Location "frontend\climaterisk"
if (-not (Test-Path "node_modules")) { npm ci; if ($LASTEXITCODE -ne 0) { Fail "npm ci failed" } }
npm run build; if ($LASTEXITCODE -ne 0) { Fail "vite build failed" }
Pop-Location
Copy-Item -Recurse "frontend\climaterisk\dist" (Join-Path $Bundle "ui")

# ---- 2. the packaging env (CLIMADA + backend deps) ---------------------------------------
if (Test-Path $PkgEnv) { Step "2/6 reusing packaging env $PkgEnv (delete it to rebuild)" }
elseif ($FromYml) {
    Step "2/6 creating the packaging env from env_climada.yml (this takes a while)"
    & $Conda env create -y -f "worker\climaterisk_worker\env_climada.yml" --prefix $PkgEnv
    if ($LASTEXITCODE -ne 0) { Fail "conda env create failed" }
} else {
    if (-not (Test-Path (Join-Path $Env "python.exe"))) { Fail "no python.exe in $Env - build it first (README: Setup) or pass -FromYml" }
    Step "2/6 cloning $Env -> $PkgEnv (hard links where possible)"
    & $Conda create -y --prefix $PkgEnv --clone (Resolve-Path $Env).Path
    if ($LASTEXITCODE -ne 0) { Fail "conda create --clone failed" }
}
$Py = Join-Path $PkgEnv "python.exe"

Step "2/6 installing the backend's dependencies into the packaging env (from pyproject.toml)"
$Deps = & $Py -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))"
if ($LASTEXITCODE -ne 0) { Fail "could not read pyproject dependencies" }
& $Py -m pip install --quiet --no-warn-script-location --upgrade conda-pack
& $Py -m pip install --quiet --no-warn-script-location $Deps.Split("`n")
if ($LASTEXITCODE -ne 0) { Fail "pip install of the backend dependencies failed" }
& $Py -c "import fastapi, uvicorn, pydantic_settings, multipart, openpyxl, climada; print('deps ok')"
if ($LASTEXITCODE -ne 0) { Fail "the packaging env cannot import the backend's dependencies" }

# ---- 3. conda-pack -> env\ ------------------------------------------------------------------
Step "3/6 conda-pack (relocatable env)"
$EnvTar = Join-Path $OutDir "env.tar.gz"
if (Test-Path $EnvTar) { Remove-Item -Force $EnvTar }
$CondaPack = Join-Path $PkgEnv "Scripts\conda-pack.exe"   # console script (the module has no __main__)
if (-not (Test-Path $CondaPack)) { Fail "conda-pack.exe not found in the packaging env" }
& $CondaPack -p $PkgEnv -o $EnvTar --n-threads -1
if ($LASTEXITCODE -ne 0) { Fail "conda-pack failed" }
if (-not $KeepPkgEnv) {
    Step "3/6 removing the packaging env (packed; -KeepPkgEnv keeps it for faster rebuilds)"
    Remove-Item -Recurse -Force $PkgEnv
    # the interpreter below must not come from the env just removed
    $Py = Join-Path $Env "python.exe"
}
$EnvOut = Join-Path $Bundle "env"
New-Item -ItemType Directory -Force $EnvOut | Out-Null
# Python's tarfile creates every parent directory; bsdtar does not for this archive's layout.
& $Py -c "import sys, tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2])" $EnvTar $EnvOut
if ($LASTEXITCODE -ne 0) { Fail "extracting env.tar.gz failed" }
Remove-Item -Force $EnvTar

# ---- 4. app\ ---------------------------------------------------------------------------------
Step "4/6 copying the runtime files -> app\"
$App = Join-Path $Bundle "app"
New-Item -ItemType Directory -Force $App | Out-Null
foreach ($d in @("src", "worker", "assets", "scripts", "docs")) {
    robocopy (Join-Path $Repo $d) (Join-Path $App $d) /E /NFL /NDL /NJH /NJS /NC /NS /NP /XD "__pycache__" ".mypy_cache" ".ruff_cache" ".pytest_cache" "node_modules" /XF "*.pyc" | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "robocopy $d failed ($LASTEXITCODE)" }
}
foreach ($f in @("pyproject.toml", "README.md", "LICENSE", ".env.example", "CLAUDE.md")) { Copy-Item (Join-Path $Repo $f) $App }
# The worker's env YAML is not needed at runtime but documents what env\ was built from.
Set-Content -Path (Join-Path $App "BUNDLE.txt") -Encoding utf8 -Value @(
    "climaterisk Windows bundle",
    "version   : $Label",
    "built     : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "env from  : $(if ($FromYml) { 'env_climada.yml' } else { $Env })",
    "machine   : $env:COMPUTERNAME ($([System.Environment]::OSVersion.VersionString))"
)

# ---- 5. launcher + README ----------------------------------------------------------------------
Step "5/6 launcher and README"
Copy-Item (Join-Path $Repo "scripts\windows_bundle\start.ps1") $Bundle
Copy-Item (Join-Path $Repo "scripts\windows_bundle\Start climaterisk.bat") $Bundle
Copy-Item (Join-Path $Repo "scripts\windows_bundle\README-WINDOWS.md") $Bundle

# ---- 6. zip ------------------------------------------------------------------------------------
if ($SkipZip) { Step "6/6 -SkipZip: bundle left at $Bundle"; exit 0 }
Step "6/6 zipping -> $Zip (bsdtar, zip64)"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
& $Tar -a -cf $Zip -C $OutDir "bundle"
if ($LASTEXITCODE -ne 0) { Fail "zip failed" }
$Mb = [math]::Round((Get-Item $Zip).Length / 1MB)
Step "done: $Zip ($Mb MB). Unzip into a SHORT path (e.g. C:\climaterisk) and double-click 'Start climaterisk.bat'."
