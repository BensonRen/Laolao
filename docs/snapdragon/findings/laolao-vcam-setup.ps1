<#
.SYNOPSIS
    One-shot "webcam + Laolao captions -> virtual camera" setup for
    Windows 11 on ARM64 (Snapdragon), using native ARM64 OBS Studio.

.DESCRIPTION
    Replaces pyvirtualcam / virtual_cam.py, which cannot work on win-arm64
    (no wheel). OBS does the compositing instead:

        [ Video Capture Device: your webcam ]   <- bottom layer
        [ Browser Source: overlay/index.html ]  <- transparent captions on top
                          |
                   OBS Virtual Camera  ->  WeChat / Zoom / Teams

    The script is idempotent: run it again any time.

    IMPORTANT ARCHITECTURE NOTE (see WS-C-obs-vcam.md):
    Windows-on-ARM64 has ONE 64-bit COM registration slot shared by native
    ARM64 processes and x64-emulated ones -- there is no per-architecture
    registry redirection. So exactly one of the two filter DLLs can own the
    "OBS Virtual Camera" CLSID at a time:

        -Arch x64    (DEFAULT) works in x64-emulated apps: WeChat, Zoom, most
                     Electron apps. Does NOT work in ARM64-native apps.
        -Arch arm64  works in ARM64-native apps only.

    If your call app cannot see the camera, re-run with the other -Arch.

.EXAMPLE
    .\laolao-vcam-setup.ps1
    .\laolao-vcam-setup.ps1 -Arch arm64
    .\laolao-vcam-setup.ps1 -Stop
    .\laolao-vcam-setup.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [ValidateSet('x64', 'arm64')]
    [string] $Arch = 'x64',

    # Where the OBS ARM64 portable zip is / will be extracted.
    [string] $ObsRoot = "$env:USERPROFILE\Downloads\laolao-tools\obs-arm64",

    # Laolao repo checkout. Defaults to three levels above this script.
    [string] $RepoRoot,

    # Caption WebSocket port that server.py listens on.
    [int]    $Port = 8765,

    [switch] $Stop,
    [switch] $Unregister,
    [switch] $NoLaunch
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$OBS_URL   = 'https://github.com/obsproject/obs-studio/releases/download/32.2.1/OBS-Studio-32.2.1-Windows-arm64.zip'
$VCAM_CLSID = '{A3FCE0F5-3493-419F-958A-ABA1250EC20B}'
$VIDEO_CAT  = '{860BB310-5D01-11d0-BD3B-00A0C911CE86}'
$WS_PORT    = 4455
$WS_PASS    = 'LaolaoVCam2026'

# DirectShow FilterData for the OBS virtual camera filter (one video output
# pin, NV12). Captured from the OBS-generated HKLM registration and verified
# byte-for-byte to drive the x64 filter correctly. Needed because regsvr32
# CANNOT register the x64 DLL on an ARM64 host (no x64 regsvr32 exists), so
# the registration has to be written by hand.
$FILTER_DATA_B64 = 'AgAAAAAAIAABAAAAAAAAADBwaTMIAAAAAAAAAAEAAAAAAAAAAAAAADB0eTMAAAAAOAAAAEgAAAB2aWRzAAAQAIAAAKoAOJtxTlYxMgAAEACAAACqADibcQ=='

function Write-Step { param([string]$m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok   { param([string]$m) Write-Host "    $m" -ForegroundColor Green }
function Write-Warn2{ param([string]$m) Write-Host "    $m" -ForegroundColor Yellow }

# ---------------------------------------------------------------- paths ----
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
}
$OverlayHtml = Join-Path $RepoRoot 'overlay\index.html'
$ObsExe      = Join-Path $ObsRoot 'bin\64bit\obs64.exe'
$ConfigDir   = Join-Path $ObsRoot 'config\obs-studio'
$DshowDir    = Join-Path $ObsRoot 'data\obs-plugins\win-dshow'

# ------------------------------------------------------------- OBS stop ----
function Stop-Obs {
    $p = Get-Process obs64 -ErrorAction SilentlyContinue
    if (-not $p) { return $false }
    foreach ($proc in $p) {
        try { $null = $proc.CloseMainWindow() } catch { }
    }
    Start-Sleep -Seconds 5
    $p = Get-Process obs64 -ErrorAction SilentlyContinue
    if ($p) { $p | Stop-Process -Force; Start-Sleep -Seconds 2 }
    return $true
}

# OBS keeps one marker file per live instance in config/obs-studio/.sentinel
# and deletes it on a clean exit. A leftover marker makes the next launch
# open a blocking "unclean shutdown / safe mode?" dialog, which would stall
# an unattended start -- so clear them before every launch.
function Clear-Sentinel {
    $sent = Join-Path $ConfigDir '.sentinel'
    if (Test-Path $sent) {
        foreach ($f in Get-ChildItem $sent -Force -ErrorAction SilentlyContinue) {
            Remove-Item -LiteralPath $f.FullName -Force -Confirm:$false -ErrorAction SilentlyContinue
        }
    }
}

# ------------------------------------------------- virtual cam registry ----
function Unregister-VCam {
    foreach ($p in @("HKCU:\SOFTWARE\Classes\CLSID\$VCAM_CLSID",
                     "HKCU:\SOFTWARE\Classes\CLSID\$VIDEO_CAT\Instance\$VCAM_CLSID")) {
        if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force -Confirm:$false }
    }
}

function Register-VCam {
    param([string]$Which)

    $dll = if ($Which -eq 'arm64') {
        Join-Path $DshowDir 'obs-virtualcam-module-arm64.dll'
    } else {
        Join-Path $DshowDir 'obs-virtualcam-module64.dll'
    }
    if (-not (Test-Path $dll)) { throw "Filter DLL not found: $dll" }

    # Per-user (HKCU) registration -- deliberately NOT HKLM: this needs no
    # administrator rights and is enough for DirectShow to find the device,
    # because HKCR merges HKCU\Software\Classes over HKLM\Software\Classes.
    $base = "HKCU:\SOFTWARE\Classes\CLSID\$VCAM_CLSID"
    New-Item -Path "$base\InprocServer32" -Force | Out-Null
    Set-ItemProperty -Path $base -Name '(default)' -Value 'OBS Virtual Camera'
    Set-ItemProperty -Path "$base\InprocServer32" -Name '(default)' -Value $dll
    Set-ItemProperty -Path "$base\InprocServer32" -Name 'ThreadingModel' -Value 'Both'

    $inst = "HKCU:\SOFTWARE\Classes\CLSID\$VIDEO_CAT\Instance\$VCAM_CLSID"
    New-Item -Path $inst -Force | Out-Null
    Set-ItemProperty -Path $inst -Name 'FriendlyName' -Value 'OBS Virtual Camera'
    Set-ItemProperty -Path $inst -Name 'CLSID' -Value $VCAM_CLSID
    Set-ItemProperty -Path $inst -Name 'FilterData' `
        -Value ([Convert]::FromBase64String($FILTER_DATA_B64)) -Type Binary

    Write-Ok "registered $Which filter: $(Split-Path $dll -Leaf)"
}

# ------------------------------------------------- obs-websocket client ----
# Used only to auto-pick the webcam, so the user never has to open OBS.
function Connect-ObsWs {
    $ws = New-Object System.Net.WebSockets.ClientWebSocket
    $ws.Options.AddSubProtocol('obswebsocket.json')
    $ct = [Threading.CancellationToken]::None
    $null = $ws.ConnectAsync([Uri]"ws://127.0.0.1:$WS_PORT", $ct).GetAwaiter().GetResult()

    $hello = Receive-ObsWs $ws
    $ident = @{ rpcVersion = 1 }
    if ($hello.d.PSObject.Properties.Name -contains 'authentication') {
        $sha = [Security.Cryptography.SHA256]::Create()
        $salt = $hello.d.authentication.salt
        $chal = $hello.d.authentication.challenge
        $secret = [Convert]::ToBase64String(
            $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($WS_PASS + $salt)))
        $ident.authentication = [Convert]::ToBase64String(
            $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($secret + $chal)))
    }
    Send-ObsWs $ws @{ op = 1; d = $ident }
    $null = Receive-ObsWs $ws          # Identified
    return ,$ws
}

function Send-ObsWs {
    param($Ws, $Obj)
    $json  = $Obj | ConvertTo-Json -Depth 12 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $seg   = New-Object 'System.ArraySegment[byte]' -ArgumentList @(,$bytes)
    $null = $Ws.SendAsync($seg, [Net.WebSockets.WebSocketMessageType]::Text, $true,
                  [Threading.CancellationToken]::None).GetAwaiter().GetResult()
}

function Receive-ObsWs {
    param($Ws)
    $buf = New-Object byte[] 262144
    $seg = New-Object 'System.ArraySegment[byte]' -ArgumentList @(,$buf)
    $sb  = New-Object Text.StringBuilder
    do {
        $r = $Ws.ReceiveAsync($seg, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
        $null = $sb.Append([Text.Encoding]::UTF8.GetString($buf, 0, $r.Count))
    } while (-not $r.EndOfMessage)
    return $sb.ToString() | ConvertFrom-Json
}

function Invoke-ObsRequest {
    param($Ws, [string]$Type, $Data = @{})
    $id = [Guid]::NewGuid().ToString()
    Send-ObsWs $Ws @{ op = 6; d = @{ requestType = $Type; requestId = $id; requestData = $Data } }
    while ($true) {
        $m = Receive-ObsWs $Ws
        if ($m.op -eq 7 -and $m.d.requestId -eq $id) { return $m.d }
    }
}

# =========================================================== entry points ==
if ($Stop) {
    Write-Step 'Stopping OBS'
    if (Stop-Obs) { Write-Ok 'OBS stopped' } else { Write-Ok 'OBS was not running' }
    Clear-Sentinel
    return
}

if ($Unregister) {
    Write-Step 'Removing the OBS Virtual Camera registration for this user'
    Unregister-VCam
    Write-Ok 'unregistered (HKCU)'
    return
}

# ------------------------------------------------------------ 1. OBS bits --
Write-Step "Checking OBS Studio (ARM64, portable) at $ObsRoot"
if (-not (Test-Path $ObsExe)) {
    Write-Warn2 'OBS not found - downloading the official ARM64 portable zip (~167 MB)'
    $zip = Join-Path ([IO.Path]::GetTempPath()) 'OBS-Studio-arm64.zip'
    $null = New-Item -ItemType Directory -Force -Path $ObsRoot
    $prev = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $OBS_URL -OutFile $zip
    $ProgressPreference = $prev
    Expand-Archive -Path $zip -DestinationPath $ObsRoot -Force
    Remove-Item $zip -Force
}
if (-not (Test-Path $ObsExe)) { throw "obs64.exe still missing under $ObsRoot" }
Write-Ok "obs64.exe present"

if (-not (Test-Path $OverlayHtml)) {
    throw "overlay\index.html not found under -RepoRoot '$RepoRoot'. Pass -RepoRoot <path to Laolao checkout>."
}

# Portable mode keeps every setting inside the OBS folder, so nothing leaks
# into %APPDATA% and the whole thing stays uninstall-by-delete.
foreach ($marker in @('obs_portable_mode.txt', 'portable_mode.txt')) {
    $mp = Join-Path $ObsRoot $marker
    if (-not (Test-Path $mp)) { New-Item -ItemType File -Path $mp -Force | Out-Null }
}
Write-Ok 'portable mode enabled'

# ------------------------------------------------- 2. register the filter --
Write-Step "Registering the virtual camera filter for -Arch $Arch (per-user, no admin)"
if (Get-Process obs64 -ErrorAction SilentlyContinue) { $null = Stop-Obs }
Unregister-VCam
Register-VCam -Which $Arch

# --------------------------------------------------- 3. write OBS config ---
Write-Step 'Writing the Laolao profile and scene collection'
$null = New-Item -ItemType Directory -Force -Path (Join-Path $ConfigDir 'basic\profiles\Laolao')
$null = New-Item -ItemType Directory -Force -Path (Join-Path $ConfigDir 'basic\scenes')
$null = New-Item -ItemType Directory -Force -Path (Join-Path $ConfigDir 'plugin_config\obs-websocket')

@"
[General]
Name=Laolao

[Video]
BaseCX=1280
BaseCY=720
OutputCX=1280
OutputCY=720
FPSType=0
FPSCommon=30
ScaleType=bicubic
ColorFormat=NV12
ColorSpace=709
ColorRange=Partial

[Output]
Mode=Simple
"@ | Set-Content (Join-Path $ConfigDir 'basic\profiles\Laolao\basic.ini') -Encoding UTF8

# Scene collection: substitute the overlay URL now, the camera id after OBS
# starts (OBS itself is the only reliable source of its device-id format).
$tpl = Join-Path $PSScriptRoot 'laolao-obs-scene.json'
if (-not (Test-Path $tpl)) { throw "Scene template missing: $tpl" }
$overlayUrl = 'file:///' + ($OverlayHtml -replace '\\', '/') + "?output=1&port=$Port"
$sceneJson  = (Get-Content $tpl -Raw).Replace('__OVERLAY_URL__', $overlayUrl).Replace('__CAMERA_ID__', '')
Set-Content (Join-Path $ConfigDir 'basic\scenes\Laolao.json') -Value $sceneJson -Encoding UTF8

# Select our profile/collection and disable the exit confirmation dialog.
$userIni = Join-Path $ConfigDir 'user.ini'
$ini = if (Test-Path $userIni) { Get-Content $userIni -Raw } else { "[General]`r`n[Basic]`r`n" }
foreach ($kv in @(@('Profile', 'Laolao'), @('ProfileDir', 'Laolao'),
                  @('SceneCollection', 'Laolao'), @('SceneCollectionFile', 'Laolao'))) {
    if ($ini -match "(?m)^$($kv[0])=.*$") { $ini = $ini -replace "(?m)^$($kv[0])=.*$", "$($kv[0])=$($kv[1])" }
    else { $ini = $ini -replace '(?m)^\[Basic\]$', "[Basic]`r`n$($kv[0])=$($kv[1])" }
}
if ($ini -match '(?m)^ConfirmOnExit=.*$') { $ini = $ini -replace '(?m)^ConfirmOnExit=.*$', 'ConfirmOnExit=false' }
else { $ini = $ini -replace '(?m)^\[General\]$', "[General]`r`nConfirmOnExit=false" }
Set-Content $userIni -Value $ini -Encoding UTF8

@"
{
  "alerts_enabled": false,
  "auth_required": true,
  "first_load": false,
  "server_enabled": true,
  "server_password": "$WS_PASS",
  "server_port": $WS_PORT
}
"@ | Set-Content (Join-Path $ConfigDir 'plugin_config\obs-websocket\config.json') -Encoding UTF8
Write-Ok 'profile + scene collection + websocket config written'

if ($NoLaunch) { Write-Step 'Done (-NoLaunch given, OBS not started)'; return }

# --------------------------------------------------------- 4. launch OBS ---
Write-Step 'Starting OBS (minimised to tray) with the virtual camera'
Clear-Sentinel
$proc = Start-Process -FilePath $ObsExe -WorkingDirectory (Split-Path $ObsExe) -PassThru `
    -ArgumentList '--portable', '--disable-updater', '--disable-missing-files-check',
                  '--collection', 'Laolao', '--profile', 'Laolao', '--scene', 'Laolao',
                  '--minimize-to-tray', '--startvirtualcam'

# Wait for the websocket server rather than sleeping a fixed amount.
$deadline = (Get-Date).AddSeconds(90)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -LocalPort $WS_PORT -State Listen -ErrorAction SilentlyContinue) { $ready = $true; break }
    if ($proc.HasExited) { throw "OBS exited during startup (code $($proc.ExitCode))" }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw 'OBS did not open its control port within 90s' }
Write-Ok "OBS running (pid $($proc.Id))"

# ------------------------------------------------ 5. auto-select a webcam ---
Write-Step 'Selecting a webcam'
$ws = Connect-ObsWs
try {
    $items = (Invoke-ObsRequest $ws 'GetInputPropertiesListPropertyItems' `
        @{ inputName = 'Webcam'; propertyName = 'video_device_id' }).responseData.propertyItems

    $cam = $items |
        Where-Object { $_.itemEnabled -and $_.itemName -ne 'OBS Virtual Camera' } |
        Select-Object -First 1
    if (-not $cam) { throw 'No webcam found. Plug one in and re-run.' }

    $null = Invoke-ObsRequest $ws 'SetInputSettings' @{
        inputName = 'Webcam'
        inputSettings = @{ video_device_id = $cam.itemValue; last_video_device_id = $cam.itemValue }
        overlay = $true
    }
    Write-Ok "camera: $($cam.itemName)"

    if (-not (Invoke-ObsRequest $ws 'GetVirtualCamStatus').responseData.outputActive) {
        $null = Invoke-ObsRequest $ws 'StartVirtualCam'
        Start-Sleep -Seconds 2
    }
    $active = (Invoke-ObsRequest $ws 'GetVirtualCamStatus').responseData.outputActive
    if ($active) { Write-Ok 'virtual camera is RUNNING' } else { Write-Warn2 'virtual camera did not start' }
} finally {
    try { $ws.Dispose() } catch { }
}

Write-Host ''
Write-Host '  Laolao virtual camera is ready.' -ForegroundColor Green
Write-Host "  In your call app pick the camera named 'OBS Virtual Camera'."
Write-Host "  Captions appear once server.py is running on port $Port."
Write-Host ''
Write-Host "  Registered for : $Arch apps  (WeChat/Zoom are x64)" -ForegroundColor DarkGray
Write-Host "  Camera missing?: re-run with -Arch $(if ($Arch -eq 'x64') { 'arm64' } else { 'x64' })" -ForegroundColor DarkGray
Write-Host "  Stop everything: .\laolao-vcam-setup.ps1 -Stop" -ForegroundColor DarkGray
