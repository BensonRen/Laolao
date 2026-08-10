<#
.SYNOPSIS
    Start Laolao on Windows 11 ARM64 (Snapdragon) - the whole product, one command.

.DESCRIPTION
    This is what Laolao-arm64.bat runs. In order:

      1. checks the setup and runs setup-arm64.ps1 if anything is missing
      2. starts the caption engine (server.py on the native ARM64 venv,
         Whisper on the Hexagon NPU, microphone captured by sounddevice)
      3. starts the camera: portable OBS ARM64 composites
             [ your webcam ]  +  [ overlay/index.html as a transparent layer ]
         and publishes the result as "OBS Virtual Camera"
      4. opens a small window showing the live captions
      5. tells you, in plain language, which camera to pick in WeChat / Zoom

    Every step probes before acting, so running this twice is harmless: an
    already-running engine and camera are reused, not duplicated.

    No administrator rights are used or needed at any point.

.EXAMPLE
    .\launch.ps1                 # start everything
    .\launch.ps1 -Stop           # stop everything
    .\launch.ps1 -Status         # report what is running
    .\launch.ps1 -Arch arm64     # camera for ARM64-native call apps instead of x64
    .\launch.ps1 -Setup          # force the first-run setup to run again
#>
[CmdletBinding()]
param(
    [string] $RepoRoot,
    [string] $ToolsRoot,

    # Which architecture of call app must be able to OPEN the camera.
    # Windows-on-ARM64 has one 64-bit COM slot, so this is either/or:
    #   x64   (default) WeChat, Zoom, Teams - they run emulated
    #   arm64 ARM64-native apps
    [ValidateSet('x64', 'arm64')]
    [string] $Arch = 'x64',

    [int]    $Port,

    [switch] $NoBrowser,
    [switch] $NoCamera,
    [switch] $Setup,
    [switch] $Stop,
    [switch] $Status
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$VCAM_CLSID  = '{A3FCE0F5-3493-419F-958A-ABA1250EC20B}'
$OBS_WS_PORT = 4455          # obs-websocket, written by laolao-vcam-setup.ps1

function Write-Step { param([string]$m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "    $m" -ForegroundColor Green }
function Write-Warn2{ param([string]$m) Write-Host "    $m" -ForegroundColor Yellow }
function Write-Bad  { param([string]$m) Write-Host "    $m" -ForegroundColor Red }

# ---------------------------------------------------------------- paths ----
if (-not $RepoRoot)  { $RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
if (-not $ToolsRoot) { $ToolsRoot = (Join-Path (Split-Path $RepoRoot -Parent) 'laolao-tools') }

$VenvPy      = Join-Path $RepoRoot '.venv-arm64\Scripts\python.exe'
$ServerPy    = Join-Path $RepoRoot 'server.py'
$OverlayHtml = Join-Path $RepoRoot 'overlay\index.html'
$SetupPs1    = Join-Path $PSScriptRoot 'setup-arm64.ps1'
$VCamPs1     = Join-Path $PSScriptRoot 'findings\laolao-vcam-setup.ps1'
$ObsRoot     = Join-Path $ToolsRoot 'obs-arm64'
$ObsExe      = Join-Path $ObsRoot 'bin\64bit\obs64.exe'
# Where the ONNX/QNN backend caches models. Must match what the backend
# computes (<repo>\..\laolao-tools\models) or its LAOLAO_MODEL_DIR override.
$ModelDir    = if ($env:LAOLAO_MODEL_DIR) { $env:LAOLAO_MODEL_DIR } else { Join-Path $ToolsRoot 'models' }
$RunDir      = Join-Path $ToolsRoot 'run'
$PidFile     = Join-Path $RunDir 'server.pid'
$LogOut      = Join-Path $RunDir 'server.log'
$LogErr      = Join-Path $RunDir 'server.err.log'
$EngineCmd   = Join-Path $RunDir 'run-engine.cmd'

if (-not (Test-Path $RunDir)) { $null = New-Item -ItemType Directory -Force -Path $RunDir }

# Port: CLI wins, else config.json, else 8765.
if (-not $Port) {
    $Port = 8765
    $cfgPath = Join-Path $RepoRoot 'config.json'
    if (Test-Path $cfgPath) {
        try {
            $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
            if ($cfg.PSObject.Properties.Name -contains 'ws_port' -and $cfg.ws_port) { $Port = [int]$cfg.ws_port }
        } catch { }
    }
}

# ------------------------------------------------------------- helpers -----
# Ask the TCP stack who is LISTENING rather than dialling the port ourselves.
# A probe connection to server.py's WebSocket is a bare TCP open with no HTTP
# upgrade, which the websockets library logs as "connection rejected (400 Bad
# Request)" - a scary line in the log for something that is only a health check.
function Test-Listening {
    param([int]$P)
    return [bool](Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue)
}

function Get-PortOwner {
    param([int]$P)
    $conn = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if (-not $conn) { return $null }
    return Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
}

function Get-VCamDll {
    foreach ($root in @('HKCU:\SOFTWARE\Classes\CLSID', 'HKLM:\SOFTWARE\Classes\CLSID')) {
        $p = "$root\$VCAM_CLSID\InprocServer32"
        if (Test-Path $p) {
            $v = (Get-ItemProperty $p).'(default)'
            if ($v) { return $v }
        }
    }
    return $null
}

function Test-VCamArch {
    param([string]$Want)
    $dll = Get-VCamDll
    if (-not $dll) { return $false }
    $leaf = Split-Path $dll -Leaf
    if ($Want -eq 'arm64') { return ($leaf -eq 'obs-virtualcam-module-arm64.dll') }
    return ($leaf -eq 'obs-virtualcam-module64.dll')
}

function Get-ObsProcess { return (Get-Process obs64 -ErrorAction SilentlyContinue | Select-Object -First 1) }

# EXACTLY ONE process may hold the physical webcam. OBS is about to take it,
# and WS-E watched the Electron shell take NotReadableError four times and then
# sit at black=true forever while OBS held the device. The two shells are
# mutually exclusive at runtime, so the OBS path actively evicts the other one
# rather than letting the user discover a dead self-view mid-call.
# Only processes whose image lives inside this checkout are touched.
function Stop-ElectronShell {
    $killed = @()
    $root = $RepoRoot.ToLower()
    foreach ($p in (Get-Process electron, Laolao -ErrorAction SilentlyContinue)) {
        $path = ''
        try { $path = "$($p.Path)".ToLower() } catch { }
        if ($path -and $path.StartsWith($root)) {
            Stop-Tree -RootPid $p.Id
            $killed += "$($p.ProcessName) (pid $($p.Id))"
        }
    }
    return $killed
}

# The engine is a small chain: run-engine.cmd -> venv python.exe -> the real
# interpreter (the venv python on Windows is a stub that re-execs its base and
# it is the CHILD that owns the socket). Killing just the pid we started leaves
# the actual server alive, so walk the tree.
function Stop-Tree {
    param([int]$RootPid)
    foreach ($k in (Get-CimInstance Win32_Process -Filter "ParentProcessId=$RootPid" -ErrorAction SilentlyContinue)) {
        Stop-Tree -RootPid ([int]$k.ProcessId)
    }
    Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

function Stop-Engine {
    $stopped = $false
    if (Test-Path $PidFile) {
        $saved = (Get-Content $PidFile -Raw).Trim()
        if ($saved -match '^\d+$' -and (Get-Process -Id ([int]$saved) -ErrorAction SilentlyContinue)) {
            Stop-Tree -RootPid ([int]$saved)
            $stopped = $true
        }
    }
    # Also take whatever actually holds the port, so a server started by hand
    # (or one that outlived a crashed wrapper) is cleaned up too.
    for ($i = 0; $i -lt 10; $i++) {
        $owner = Get-PortOwner $Port
        if (-not $owner) { break }
        if ($owner.ProcessName -notmatch '^(python|pythonw|cmd)$') { break }
        Stop-Tree -RootPid $owner.Id
        $stopped = $true
        Start-Sleep -Milliseconds 300
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    return $stopped
}

# ============================================================ -Status ======
if ($Status) {
    $owner = Get-PortOwner $Port
    $obs   = Get-ObsProcess
    $dll   = Get-VCamDll
    Write-Host ''
    Write-Host "  caption engine : $(if ($owner) { "running (pid $($owner.Id)) on port $Port" } else { 'not running' })"
    Write-Host "  OBS / camera   : $(if ($obs) { "running (pid $($obs.Id))" } else { 'not running' })"
    Write-Host "  camera filter  : $(if ($dll) { Split-Path $dll -Leaf } else { 'not registered' })"
    Write-Host ''
    exit 0
}

# ============================================================== -Stop ======
if ($Stop) {
    Write-Step 'Stopping Laolao'
    if (Stop-Engine) { Write-Ok 'caption engine stopped' } else { Write-Ok 'caption engine was not running' }
    if (Test-Path $VCamPs1) {
        try { & $VCamPs1 -Stop -RepoRoot $RepoRoot -ObsRoot $ObsRoot | Out-Null } catch { }
    }
    if (Get-ObsProcess) {
        Get-Process obs64 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Write-Ok 'camera stopped'
    Write-Host ''
    Write-Host '  Laolao is stopped. Your normal webcam is free again.' -ForegroundColor Green
    Write-Host '  (You can close the black caption window if it is still open.)'
    Write-Host ''
    exit 0
}

# ============================================================== start ======
Write-Host ''
Write-Host '  Laolao - live Chinese captions on your camera' -ForegroundColor White
Write-Host '  Windows on ARM64 / Snapdragon' -ForegroundColor DarkGray
Write-Host ''

# ------------------------------------------------------- 1. setup check ----
Write-Step '[1/4] Checking that everything is installed'

# Only things setup-arm64.ps1 owns are checked here. Everything camera-shaped
# (downloading OBS, registering the filter for the right architecture) is step
# 3's job and is re-checked there, so it is deliberately NOT duplicated into
# this list - doing both meant registering the filter twice on a first run.
$missing = @()
if (-not (Test-Path $ServerPy))    { $missing += 'server.py (is this the Laolao folder?)' }
if (-not (Test-Path $OverlayHtml)) { $missing += 'overlay/index.html' }
if (-not (Test-Path $VenvPy)) {
    $missing += 'the Python environment'
} else {
    & $VenvPy -c 'import onnxruntime,numpy,sounddevice,websockets,tokenizers,opencc' 2>$null
    if ($LASTEXITCODE -ne 0) { $missing += 'some Python packages' }
}
# The Whisper NPU asset is a ~180 MB download that onnxruntime-qnn fetches over
# plain urllib on first use. Left to happen at launch it is an invisible
# multi-minute stall with the call already ringing, so drag it into setup where
# it can be announced.
if (-not (Get-ChildItem $ModelDir -Directory -ErrorAction SilentlyContinue)) {
    $missing += 'the speech model (a one-time download)'
}

if ($Setup -or $missing.Count) {
    if ($missing.Count) { Write-Warn2 "first run - still needed: $($missing -join ', ')" }
    else                { Write-Warn2 '-Setup given: running setup again' }
    Write-Warn2 'this happens once and can take a few minutes'
    Write-Host ''
    & $SetupPs1 -RepoRoot $RepoRoot -ToolsRoot $ToolsRoot -Arch $Arch -Force:$Setup -SkipCamera
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Bad "Setup could not finish (code $LASTEXITCODE). Nothing was started."
        Write-Bad 'Scroll up - the step that failed printed what to do.'
        exit 1
    }
    Write-Host ''
} else {
    Write-Ok 'Python environment and speech packages are in place'
}

# ---------------------------------------------------- 2. caption engine ----
Write-Step '[2/4] Starting the caption engine'

$owner = Get-PortOwner $Port
if ($owner -and $owner.ProcessName -notmatch '^python') {
    Write-Bad "Port $Port is already used by $($owner.ProcessName) (pid $($owner.Id)), which is not Laolao."
    Write-Bad "Close that program, or set a different ws_port in config.json."
    exit 2
}
if ($owner) {
    Write-Ok "already running (pid $($owner.Id)) - reusing it"
    Set-Content $PidFile -Value $owner.Id -Encoding ASCII
} else {
    foreach ($f in @($LogOut, $LogErr)) { Set-Content $f -Value '' -Encoding UTF8 -ErrorAction SilentlyContinue }

    # Launch through a tiny .cmd that does its own redirection, rather than
    # Start-Process -RedirectStandardOutput.
    #
    # Redirecting via Start-Process makes PowerShell create the child with
    # bInheritHandles=TRUE, so the engine also inherits whatever stdout/stderr
    # the *launcher* was given. When something scripted runs Laolao-arm64.bat
    # and captures its output, that captured pipe is then held open by a
    # long-lived background server, and the caller blocks on EOF forever even
    # though the launcher itself finished seconds ago. Verified: the acceptance
    # harness hung for 10 minutes on exactly this. ShellExecute (what
    # Start-Process does with no redirection) inherits nothing.
    $modelEnv = if ($env:LAOLAO_MODEL_DIR) { "set LAOLAO_MODEL_DIR=$env:LAOLAO_MODEL_DIR" } else { 'rem model dir: backend default' }
    @"
@echo off
cd /d "$RepoRoot"
set PYTHONIOENCODING=utf-8
$modelEnv
"$VenvPy" server.py > "$LogOut" 2> "$LogErr"
"@ | Set-Content $EngineCmd -Encoding ASCII

    $proc = Start-Process -FilePath $EngineCmd -WorkingDirectory $RepoRoot `
                -WindowStyle Hidden -PassThru
    Set-Content $PidFile -Value $proc.Id -Encoding ASCII

    # First ever start loads the Whisper NPU context, which is slow; a warm
    # start is a couple of seconds. Wait on the port, not on a guess.
    $deadline = (Get-Date).AddSeconds(300)
    $ready = $false
    Write-Host '    ' -NoNewline
    while ((Get-Date) -lt $deadline) {
        if (Test-Listening $Port) { $ready = $true; break }
        if ($proc.HasExited) { break }
        Write-Host '.' -NoNewline
        Start-Sleep -Milliseconds 700
    }
    Write-Host ''
    if (-not $ready) {
        Write-Bad 'The caption engine did not start.'
        $tail = @()
        foreach ($f in @($LogErr, $LogOut)) {
            if (Test-Path $f) { $tail += (Get-Content $f -Tail 12 -Encoding UTF8 -ErrorAction SilentlyContinue) }
        }
        foreach ($l in $tail) { Write-Bad "  $l" }
        Write-Bad "Full log: $LogOut"
        Stop-Engine | Out-Null
        exit 2
    }
    Write-Ok "listening on port $Port (pid $($proc.Id))"
}

# ------------------------------------------------------------ 3. camera ----
if ($NoCamera) {
    Write-Step '[3/4] Camera skipped (-NoCamera)'
} else {
    Write-Step '[3/4] Starting the camera'
    # @() because a function returning nothing yields $null, not an empty
    # array, and under Set-StrictMode $null.Count is a hard error.
    $evicted = @(Stop-ElectronShell)
    if ($evicted.Count) {
        Write-Warn2 "closed the Laolao app window ($($evicted -join ', ')) - only one"
        Write-Warn2 'program can hold the webcam, and OBS is taking it'
    }
    $obs = Get-ObsProcess
    if ($obs -and (Test-Listening $OBS_WS_PORT) -and (Test-VCamArch $Arch)) {
        Write-Ok "already running (pid $($obs.Id)) - reusing it"
    } else {
        try {
            & $VCamPs1 -Arch $Arch -RepoRoot $RepoRoot -ObsRoot $ObsRoot -Port $Port
        } catch {
            Write-Bad "The camera could not start: $($_.Exception.Message)"
            Write-Bad 'Captions still work - but call apps will not see them.'
            Write-Bad "Is a webcam plugged in? Then run:  Laolao-arm64.bat -Setup"
            exit 3
        }
    }
}

# -------------------------------------------------- 4. the caption window ---
Write-Step '[4/4] Opening the caption window'
$overlayUrl = 'file:///' + ($OverlayHtml -replace '\\', '/') + "?output=1&port=$Port"
if ($NoBrowser) {
    Write-Ok "skipped (-NoBrowser). It lives at: $overlayUrl"
} else {
    # `?output=1` is the chrome-free display-only overlay: no toolbar, and it
    # never opens the webcam or the microphone, so it cannot fight with OBS
    # over the camera or double-feed audio to the engine.
    $edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
              "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
            Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    $opened = $false
    if ($edge) {
        # --user-data-dir is not optional. Without it this shares the user's
        # Edge profile, and if that profile is already locked by another Edge
        # process the new one aborts with "Lock file can not be created!" and
        # no window ever appears. Our own tiny profile also keeps the caption
        # window free of their extensions, tabs and session restore.
        $profileDir = Join-Path $RunDir 'caption-window-profile'
        try {
            Start-Process $edge -ArgumentList @(
                "--app=$overlayUrl", '--window-size=920,340',
                "--user-data-dir=$profileDir",
                '--no-first-run', '--no-default-browser-check') | Out-Null
            $opened = $true
        } catch { }
    }
    if (-not $opened) {
        try { Start-Process $overlayUrl | Out-Null; $opened = $true } catch { }
    }
    if ($opened) { Write-Ok 'a small black window with your captions should appear' }
    else         { Write-Warn2 "could not open a browser - open this yourself: $overlayUrl" }
}

# ------------------------------------------------------------ the pitch ----
$camWord = if ($Arch -eq 'x64') { 'WeChat, Zoom and Teams' } else { 'ARM64-native call apps' }
Write-Host ''
Write-Host '  ============================================================' -ForegroundColor Green
Write-Host '   Laolao is running.' -ForegroundColor Green
Write-Host '  ============================================================' -ForegroundColor Green
Write-Host ''
Write-Host '   1. Open WeChat / Zoom / Teams and go to its video settings.'
Write-Host '   2. For the camera, choose:  ' -NoNewline
Write-Host 'OBS Virtual Camera' -ForegroundColor White
Write-Host '   3. Start speaking. Big Chinese subtitles appear on your video.'
Write-Host ''
Write-Host '   Already had the call app open? Close it completely and reopen it -'
Write-Host '   call apps only look for cameras when they start.'
Write-Host ''
Write-Host '   To stop Laolao: double-click Laolao-stop.bat' -ForegroundColor DarkGray
Write-Host "   Camera set up for: $camWord" -ForegroundColor DarkGray
Write-Host "   Camera in the list but the picture is black? Run:  Laolao-arm64.bat -Arch $(if ($Arch -eq 'x64') { 'arm64' } else { 'x64' })" -ForegroundColor DarkGray
Write-Host "   Engine log: $LogOut" -ForegroundColor DarkGray
Write-Host ''
exit 0
