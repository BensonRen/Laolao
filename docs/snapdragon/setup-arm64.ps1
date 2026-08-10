<#
.SYNOPSIS
    First-run setup for Laolao on Windows 11 ARM64 (Snapdragon).

.DESCRIPTION
    Everything a clean machine needs, with no administrator rights:

      1. find a NATIVE ARM64 Python 3.10+          (the one thing a human installs)
      2. create the .venv-arm64 virtual environment
      3. install requirements-arm64.txt with --only-binary=:all:
         (deliberately NOT silero-vad / faster-whisper / pyvirtualcam - see that file)
      4. download the Whisper NPU model once so the first launch is not a surprise
      5. download portable OBS ARM64 and register the virtual camera per-user,
         by delegating to findings\laolao-vcam-setup.ps1

    Idempotent: every step probes first and skips work that is already done, so
    re-running costs a couple of seconds. `-Force` redoes the venv and the pip
    install anyway.

    You normally do not run this yourself - Laolao-arm64.bat runs it for you the
    first time, and any time something is missing.

.EXAMPLE
    .\setup-arm64.ps1
    .\setup-arm64.ps1 -Force
    .\setup-arm64.ps1 -SkipModel -SkipCamera     # deps only
#>
[CmdletBinding()]
param(
    # Laolao checkout. Defaults to two levels above this script.
    [string] $RepoRoot,

    # Where downloads that are NOT part of the repo live (OBS, models).
    [string] $ToolsRoot,

    # Which architecture of the OBS DirectShow filter to register.
    # x64 = WeChat / Zoom / most call apps.  See laolao-vcam-setup.ps1.
    [ValidateSet('x64', 'arm64')]
    [string] $Arch = 'x64',

    [switch] $Force,
    [switch] $SkipModel,
    [switch] $SkipCamera
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step { param([string]$m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "    $m" -ForegroundColor Green }
function Write-Warn2{ param([string]$m) Write-Host "    $m" -ForegroundColor Yellow }
function Write-Bad  { param([string]$m) Write-Host "    $m" -ForegroundColor Red }

# ---------------------------------------------------------------- paths ----
if (-not $RepoRoot)  { $RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
if (-not $ToolsRoot) { $ToolsRoot = (Join-Path (Split-Path $RepoRoot -Parent) 'laolao-tools') }

$VenvDir  = Join-Path $RepoRoot '.venv-arm64'
$VenvPy   = Join-Path $VenvDir  'Scripts\python.exe'
$ReqFile  = Join-Path $RepoRoot 'requirements-arm64.txt'
$VCamPs1  = Join-Path $PSScriptRoot 'findings\laolao-vcam-setup.ps1'

Write-Host ''
Write-Host '  Laolao - first-run setup for Windows on ARM64' -ForegroundColor White
Write-Host "  repo : $RepoRoot"  -ForegroundColor DarkGray
Write-Host "  tools: $ToolsRoot" -ForegroundColor DarkGray
Write-Host ''

# ------------------------------------------------------- 0. sanity check ---
# An x64-emulated PowerShell would happily install x64 wheels into an
# "ARM64" venv and lose the NPU, so refuse to guess.
$osArch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
if ("$osArch" -ne 'Arm64') {
    Write-Bad "This machine reports OSArchitecture=$osArch, not Arm64."
    Write-Bad 'Use setup.bat (the normal Windows path) instead.'
    exit 1
}
if (-not (Test-Path (Join-Path $RepoRoot 'server.py'))) {
    Write-Bad "server.py not found under -RepoRoot '$RepoRoot'."
    exit 1
}

# ------------------------------------------------- 1. find ARM64 Python ----
# Returns "<machine> <major>.<minor>" or $null for anything that will not run.
function Get-PyInfo {
    param([string]$Exe)
    if (-not (Test-Path $Exe)) { return $null }
    try {
        $out = & $Exe -c "import platform,sys;print(platform.machine()+' '+str(sys.version_info[0])+'.'+str(sys.version_info[1]))" 2>$null
    } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    return ("$out").Trim()
}

function Test-PyUsable {
    param([string]$Info)
    if (-not $Info) { return $false }
    $parts = $Info -split '\s+'
    if ($parts.Count -lt 2) { return $false }
    if ($parts[0].ToUpper() -ne 'ARM64') { return $false }
    $v = [version]$parts[1]
    return ($v -ge [version]'3.10')
}

Write-Step 'Looking for a native ARM64 Python 3.10+'

$pyCandidates = New-Object System.Collections.Generic.List[string]
if ($env:LAOLAO_PYTHON) { $pyCandidates.Add($env:LAOLAO_PYTHON) }
foreach ($base in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python",
                    "${env:ProgramFiles(Arm)}\Python", 'C:\Python')) {
    if ($base -and (Test-Path $base)) {
        # Prefer the *-arm64 suffixed installs, then anything else, newest first.
        Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
            Sort-Object @{ e = { $_.Name -like '*arm64' } }, Name -Descending |
            ForEach-Object { $pyCandidates.Add((Join-Path $_.FullName 'python.exe')) }
    }
}
foreach ($cmd in @('python.exe', 'python3.exe')) {
    foreach ($hit in (Get-Command $cmd -All -ErrorAction SilentlyContinue)) {
        # The Microsoft Store alias is a 0-byte stub that opens the Store.
        if ($hit.Source -and $hit.Source -notmatch 'WindowsApps') { $pyCandidates.Add($hit.Source) }
    }
}

$SystemPy = $null
$rejected = @()
foreach ($cand in $pyCandidates) {
    $info = Get-PyInfo $cand
    if (Test-PyUsable $info) { $SystemPy = $cand; Write-Ok "$cand  ($info)"; break }
    elseif ($info) { $rejected += "$cand ($info)" }
}

if (-not $SystemPy) {
    # Maybe the venv already exists and its base python has since moved/gone -
    # that is still fine, we can reuse the venv itself.
    if ((Test-PyUsable (Get-PyInfo $VenvPy)) -and -not $Force) {
        Write-Warn2 'no system Python found, but .venv-arm64 already works - reusing it'
    } else {
        Write-Bad 'No native ARM64 Python 3.10 or newer was found.'
        foreach ($r in $rejected) { Write-Bad "  rejected: $r" }
        Write-Host ''
        Write-Host '  Laolao needs Python. Install it once (no admin required):' -ForegroundColor White
        Write-Host '    1. Open  https://www.python.org/downloads/windows/'
        Write-Host '    2. Download "Windows installer (ARM64)" for Python 3.11 or 3.12.'
        Write-Host '       The ARM64 build matters - an x64 Python cannot reach the'
        Write-Host '       Snapdragon NPU and Laolao would be about 25x slower.'
        Write-Host '    3. Tick "Add python.exe to PATH", click Install Now.'
        Write-Host '    4. Run this again (or just double-click Laolao-arm64.bat).'
        Write-Host ''
        exit 2
    }
}

# ---------------------------------------------------------- 2. the venv ----
Write-Step 'Python virtual environment (.venv-arm64)'
if ($Force -and (Test-Path $VenvDir)) {
    Write-Warn2 '-Force: removing the existing venv'
    Remove-Item $VenvDir -Recurse -Force -Confirm:$false
}
if (-not (Test-PyUsable (Get-PyInfo $VenvPy))) {
    if (Test-Path $VenvDir) { Remove-Item $VenvDir -Recurse -Force -Confirm:$false }
    Write-Warn2 'creating it (a few seconds)'
    & $SystemPy -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Bad "python -m venv failed ($LASTEXITCODE)"; exit 3 }
}
$venvInfo = Get-PyInfo $VenvPy
if (-not (Test-PyUsable $venvInfo)) { Write-Bad "the venv interpreter is not usable ($venvInfo)"; exit 3 }
Write-Ok ".venv-arm64 ready ($venvInfo)"

# ------------------------------------------------------ 3. dependencies ----
# One import probe decides whether pip needs to run at all - re-running setup
# on an already-good machine should cost seconds, not minutes.
$IMPORT_PROBE = 'import onnxruntime,numpy,sounddevice,websockets,tokenizers,opencc,huggingface_hub,httpx,PIL'

Write-Step 'Python packages'
$needPip = $Force
if (-not $needPip) {
    & $VenvPy -c $IMPORT_PROBE 2>$null
    $needPip = ($LASTEXITCODE -ne 0)
}
if ($needPip) {
    if (-not (Test-Path $ReqFile)) { Write-Bad "missing $ReqFile"; exit 4 }
    Write-Warn2 'installing (first run downloads ~120 MB of wheels)'
    & $VenvPy -m pip install --quiet --disable-pip-version-check --upgrade pip
    & $VenvPy -m pip install --only-binary=:all: --disable-pip-version-check -r $ReqFile
    if ($LASTEXITCODE -ne 0) { Write-Bad "pip install failed ($LASTEXITCODE)"; exit 4 }
    & $VenvPy -c $IMPORT_PROBE
    if ($LASTEXITCODE -ne 0) { Write-Bad 'packages installed but still not importable'; exit 4 }
}
Write-Ok 'onnxruntime-qnn, sounddevice, websockets, tokenizers, opencc, Pillow present'
Write-Ok 'skipped by design: silero-vad, faster-whisper, pyvirtualcam (no win-arm64 build)'

# ------------------------------------------------- 4. pre-fetch the model ---
# Doing this here rather than on the first launch means "start Laolao" never
# turns into a silent 3-minute download while grandma waits on the call.
if (-not $SkipModel) {
    Write-Step 'Speech model for the Hexagon NPU'

    # Long paths break the QNN asset extraction SILENTLY: the model falls back
    # to the CPU provider and runs ~25x slower with nothing in the log to say
    # why. Cheap to check, impossible to diagnose later.
    $ModelDir = if ($env:LAOLAO_MODEL_DIR) { $env:LAOLAO_MODEL_DIR } else { Join-Path $ToolsRoot 'models' }
    if ($ModelDir.Length -gt 90) {
        Write-Bad  "The model folder path is $($ModelDir.Length) characters long:"
        Write-Bad  "  $ModelDir"
        Write-Bad  'Deep paths make the NPU model extract incompletely, and the only'
        Write-Bad  'symptom is that captions become ~25x slower. Move Laolao closer to'
        Write-Bad  'the root of the drive, or set LAOLAO_MODEL_DIR to something short:'
        Write-Bad  '  setx LAOLAO_MODEL_DIR C:\laolao-models'
        Write-Warn2 'continuing anyway - but do not trust the latency numbers'
    }

    Write-Warn2 'one-time download of about 200 MB - this needs internet.'
    Write-Warn2 'After this, Laolao never touches the network again.'
    $probe = Join-Path ([IO.Path]::GetTempPath()) 'laolao_warm_model.py'
    @'
import json, os, sys, time
sys.path.insert(0, os.getcwd())
from backends import get_backend
cfg = json.load(open("config.json", encoding="utf-8"))
t0 = time.time()
be = get_backend(cfg)
print("BACKEND_OK %s in %.1fs" % (be.name, time.time() - t0))
'@ | Set-Content $probe -Encoding UTF8
    $env:PYTHONIOENCODING = 'utf-8'
    Push-Location $RepoRoot
    try {
        & $VenvPy $probe
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 'could not load the model yet - Laolao will retry at launch'
        } else {
            Write-Ok 'model downloaded and the backend loads'
        }
    } finally {
        Pop-Location
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------ 5. camera (OBS ARM64) ----
if (-not $SkipCamera) {
    Write-Step 'Virtual camera (portable OBS ARM64, no admin, no installer)'
    if (-not (Test-Path $VCamPs1)) { Write-Bad "missing $VCamPs1"; exit 5 }
    # laolao-vcam-setup.ps1 signals failure by throwing, not by exit code.
    try {
        & $VCamPs1 -Arch $Arch -RepoRoot $RepoRoot `
                   -ObsRoot (Join-Path $ToolsRoot 'obs-arm64') -NoLaunch
    } catch {
        Write-Bad "camera setup failed: $($_.Exception.Message)"
        exit 5
    }
}

Write-Host ''
Write-Host '  Setup complete.' -ForegroundColor Green
Write-Host '  Double-click Laolao-arm64.bat to start Laolao.'
Write-Host ''
exit 0
