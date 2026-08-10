# WS-C — OBS ARM64 + virtual camera

**Verdict: the approach WORKS.** Native ARM64 OBS Studio composites
`overlay/index.html` (browser source) over the webcam and publishes it as
"OBS Virtual Camera", which an **x64-emulated** consumer — the same
architecture as WeChat and Zoom — opens and reads live pixels from.
`pyvirtualcam` / `virtual_cam.py` are not needed on ARM64.

**The one sharp edge (H-303):** Windows-on-ARM64 has a *single* 64-bit COM
registration slot shared by ARM64-native and x64-emulated processes. Exactly
one filter DLL can own the camera CLSID at a time. Register the **x64** one
(default) and WeChat/Zoom work while ARM64-native apps cannot open it; register
the ARM64 one and the reverse. There is no way to satisfy both with the DLLs
OBS ships.

Environment: Snapdragon X2 Elite, Windows 11 build 28000 ARM64, OBS 32.2.1
portable at `C:\Users\snapd\Downloads\laolao-tools\obs-arm64\` (outside the repo).

| ID | Hypothesis | Status |
|---|---|---|
| H-300 | OBS ARM64 portable zip runs on this machine | **CONFIRMED** |
| H-301 | Virtual camera registers without the NSIS installer | **CONFIRMED** (and *without admin*) |
| H-302 | Registered camera enumerates for ARM64 consumers | **CONFIRMED** (when the ARM64 DLL is the registered one) |
| H-303 | Camera is usable by x64-emulated call apps | **CONFIRMED, but mutually exclusive with H-302** |
| H-304 | `overlay/index.html` works as a transparent OBS browser source | **CONFIRMED** (needs a CSS override) |
| H-305 | OBS browser source + virtual cam replaces `virtual_cam.py` | **CONFIRMED** |

Deliverables in this folder:
- `laolao-vcam-setup.ps1` — one-command setup (downloads OBS if absent, registers
  the filter per-user, writes config, launches, auto-picks the webcam, starts the cam)
- `laolao-obs-scene.json` — the scene collection template it installs
- `WS-C-evidence-vcam-x64-frame.jpg` — frame captured *out of the virtual camera by an x64 process*

---

## H-300 — OBS ARM64 runs. CONFIRMED

Downloaded the official asset (`gh api` listing → `Invoke-WebRequest`); the file
matches the release size exactly (167,106,178 bytes), extracted with
`Expand-Archive`. Binaries are genuine ARM64 (PE machine `0xAA64`):

```
obs64.exe                        machine=0xAA64  ARM64
obs-plugins/64bit/win-dshow.dll  machine=0xAA64  ARM64
```

Portable mode via an empty `obs_portable_mode.txt` in the OBS root works — config
landed in `obs-arm64\config\obs-studio\` and never touched `%APPDATA%`.

From `config/obs-studio/logs/2026-08-09 23-57-08.txt`:

```
CPU Name: Snapdragon(R) X2 Elite - X2E88100 - Qualcomm Oryon(TM) CPU
Windows Version: 10.0 Build 28000 (release: 26H1; ARM 64-bit)
Portable mode: true
OBS 32.2.1 (64-bit, windows)
Initializing D3D11...
	Adapter 0: Qualcomm(R) Adreno(TM) X2-90 GPU
D3D11 loaded successfully, feature level used: b000
[obs-browser]: Version 2.26.9
[obs-browser]: CEF Version 127.0.6533.120 (runtime)
[obs-websocket] ... (Version: 5.7.4 | RPC Version: 1)
==== Startup complete ===============================================
```

Everything Laolao needs is present and native: **D3D11 on the Adreno GPU**,
**obs-browser (CEF 127)** for the caption overlay, **win-dshow** for the virtual
camera, and **obs-websocket** which makes the whole setup scriptable.

### Two launch gotchas worth knowing

1. **There is no `--disable-shutdown-check` flag.** The full flag list extracted
   from `obs64.exe` is: `--allow-opengl --always-on-top --branch --collection
   --config-url --disable-missing-files-check --disable-updater --help
   --minimize-to-tray --multi --only-bundled-plugins --portable --profile
   --safe-mode --scene --startrecording --startreplaybuffer --startstreaming
   --startvirtualcam --steam --studio-mode --unfiltered --verbose --version`.
2. **`config/obs-studio/.sentinel` is a directory**, holding one `run_<uuid>`
   marker per live instance, deleted on clean exit. A leftover marker makes the
   next launch stop at a blocking *"Crash or unclean shutdown detected"* safe-mode
   dialog — the log is then exactly 50 bytes and OBS never finishes starting.
   The setup script clears those markers before every launch.

---

## H-301 — Registration without the installer. CONFIRMED, and no admin needed

The ARM64 zip ships **three** filter DLLs plus the same install script as the x64 build:

```
data/obs-plugins/win-dshow/obs-virtualcam-module-arm64.dll   230160   machine=0xAA64 ARM64
data/obs-plugins/win-dshow/obs-virtualcam-module64.dll       238864   machine=0x8664 x64
data/obs-plugins/win-dshow/obs-virtualcam-module32.dll       197904   machine=0x014C x86
data/obs-plugins/win-dshow/virtualcam-install.bat              2153
```

`virtualcam-install.bat` is **byte-identical to the x64 build's** (SHA256 match)
and only ever registers `module32`/`module64` — it never mentions the ARM64 DLL.

`regsvr32` works for the two DLLs whose architecture has a host binary:

```
regsvr32 (C:\Windows\System32,  ARM64) + obs-virtualcam-module-arm64.dll -> exit 0, keys created
regsvr32 (C:\Windows\SysWOW64,  x86)   + obs-virtualcam-module32.dll     -> exit 0, keys created
```

The registration is small — this is the entire thing:

```
HKLM\...\Classes\CLSID\{A3FCE0F5-3493-419F-958A-ABA1250EC20B}
    (default)       = OBS Virtual Camera
  \InprocServer32
    (default)       = <path to the filter DLL>
    ThreadingModel  = Both
HKLM\...\Classes\CLSID\{860BB310-5D01-11d0-BD3B-00A0C911CE86}\Instance\{A3FCE0F5-...}
    FriendlyName    = OBS Virtual Camera
    CLSID           = {A3FCE0F5-3493-419F-958A-ABA1250EC20B}
    FilterData      = <88 bytes>
```

### You cannot regsvr32 the x64 filter on ARM64

There is no x64 `regsvr32` on this machine (`C:\Windows` has only `System32`
(ARM64) and `SysWOW64` (x86) — no x64 system directory), and an ARM64 process
cannot load an x64 DLL to call its `DllRegisterServer`. Both attempts report
exit code 0 (silent mode always does) but write nothing:

```
System32 (ARM64) regsvr32 + obs-virtualcam-module64.dll -> exit 0
SysWOW64 (x86)   regsvr32 + obs-virtualcam-module64.dll -> exit 0
HKLM CLSID created by either? False
```

So registering the x64 filter **must** be done by writing the registry keys
directly. That is what `laolao-vcam-setup.ps1` does, reusing the 88-byte
`FilterData` blob captured from OBS's own registration (verified to drive the
x64 filter correctly — frames flow, see H-303).

### No administrator required

Registration was moved from `HKLM\SOFTWARE\Classes` to
**`HKCU\SOFTWARE\Classes`** and re-tested with HKLM entirely removed. `HKEY_CLASSES_ROOT`
merges HKCU over HKLM, so DirectShow resolves it identically:

```
###### x64 consumer with HKCU-ONLY (no-admin) registration ######
[direct] CoCreateInstance(OBS VCam CLSID, IBaseFilter) -> S_OK
[direct] EnumPins -> S_OK, pin count = 1
--- VideoInputDeviceCategory devices ---
  1. ASUS FHD webcam               BindToObject=S_OK   pins=1
  2. OBS Virtual Camera            BindToObject=S_OK   pins=1
```

and real frames were captured through it (`vcam-hkcu.png`, 1,026,320 bytes).
**This matters for A10** — grandma's machine needs no elevation and no installer.

---

## H-302 / H-303 — who can actually open the camera

Tested with two independent, genuinely-different-architecture consumers:

- a raw-ctypes DirectShow probe (`ICreateDevEnum` → `IEnumMoniker` →
  `IPropertyBag` FriendlyName → `IMoniker::BindToObject` → `EnumPins`) run under
  **ARM64 python 3.11** and **x64 python 3.11**, and
- **ffmpeg** `winarm64` and `win64` static builds (`-f dshow -list_devices`,
  then a real capture).

### The finding that decides everything: no registry redirection for x64

An x64-emulated process on ARM64 sees the *same* registry view as an ARM64
process. `HKLM\SOFTWARE\Classes\Wow6464Node` exists but COM does **not** consult
it for in-proc server resolution (writing the x64 DLL there had zero effect):

```
===== x64 emulated process =====        ===== ARM64 native process =====
machine = AMD64                         machine = ARM64
 HKLM Classes\CLSID  (default view)  -> obs-virtualcam-module-arm64.dll   (both)
 HKLM Classes\CLSID  KEY_WOW64_64KEY -> obs-virtualcam-module-arm64.dll   (both)
 HKLM Classes\CLSID  KEY_WOW64_32KEY -> obs-virtualcam-module32.dll       (both)
 HKLM Classes\Wow6464Node            -> obs-virtualcam-module64.dll       (both, ignored by COM)
 HKCR CLSID (merged view)            -> obs-virtualcam-module-arm64.dll   (both)
```

(`Wow6464Node` is real but appears to be used only by components that read it
explicitly — the only CLSID in it on this machine is Defender's AMSI category.)

So the CLSID slot is a single seat, and the two architectures trade places:

| Registered DLL | ARM64-native consumer | x64-emulated consumer (WeChat/Zoom) |
|---|---|---|
| `obs-virtualcam-module-arm64.dll` | `BindToObject=S_OK`, pins=1 | `ERROR_BAD_EXE_FORMAT (0x800700C1)` |
| `obs-virtualcam-module64.dll` | `ERROR_BAD_EXE_FORMAT (0x800700C1)` | `BindToObject=S_OK`, pins=1 |

Note the failure mode: the device still **enumerates and shows up in the picker**
under both settings — it just cannot be opened. ffmpeg lists it as `(none)`
instead of `(video)`, and the ARM64 build fails with:

```
[in#0] Unable to BindToObject for OBS Virtual Camera
[in#0] Could not find video device with name [OBS Virtual Camera] among source devices of type video.
```

A user whose app is the wrong architecture sees "OBS Virtual Camera" in the list
and gets a dead/black feed — worth calling out in the troubleshooting docs.

**H-302: CONFIRMED** — with the ARM64 DLL registered, an ARM64 consumer binds the
filter and gets a pin.
**H-303: CONFIRMED for x64 apps, at the cost of ARM64 apps.** Because WeChat and
Zoom on Windows-ARM64 are x64-emulated, the setup script registers the **x64**
filter by default, and `-Arch arm64` flips it.

### Could both work at once?

Only with an **ARM64X** DLL (one PE containing both ARM64 and x64 code, which is
how `C:\Windows\System32` DLLs serve emulated processes). OBS does not ship one —
`obs-virtualcam-module-arm64.dll` is pure ARM64, proven by the x64 load failing
with `ERROR_BAD_EXE_FORMAT`. Building one needs `link /machine:arm64x`, i.e. an
MSVC toolchain, which this machine does not have (H-010 REFUTED). **Not solvable
locally**; upstream OBS shipping an ARM64X filter would fix it for everyone.

---

## H-304 — overlay as a transparent browser source. CONFIRMED

Scene: `Video Capture Device` (webcam) on the bottom, `Browser Source`
→ `file:///…/overlay/index.html?output=1&port=8765` on top, both at 1280×720
with `bounds_type: 2` (scale-inner) so any webcam resolution is letterboxed into
the frame. OBS reports the webcam opening at 1920×1080 NV12 30fps and scales it.

**The overlay is NOT transparent out of the box.** `?output=1` correctly hides
every piece of chrome (toolbar, panels, picker, banners), but the stylesheet sets
`html, body { background: #000 }` *and* output mode assigns an inline black
`body` background in JS — the captions layer renders as an opaque black rectangle
that completely hides the webcam. First attempt: the scene composite PNG was
byte-identical in size to the captions-only PNG (28,305 bytes both), i.e. the
webcam was fully covered.

Fix, applied as the browser source's Custom CSS (both roots, `!important` to beat
the inline style):

```css
html, body { background: transparent !important; margin: 0px auto; overflow: hidden; }
#viewport, #stage-wrap { background: transparent !important; }
#camera, #preview, #preview-wait, #picker, #toolbar, #toolbar-edge,
#onboard, #mic-warn, #safe-overlay, #drag-grip { display: none !important; }
```

`#camera` is hidden so the browser source never opens the webcam itself — OBS's
own capture source owns the device, avoiding the double-`getUserMedia` problem
the repo already documents. The caption block keeps its translucent `--bg` box,
so no chroma key is needed. (The overlay's built-in `?chromakey` mode also works
but forces a green screen plus a Chroma Key filter, which is worse for text edges.)

The overlay connected to a stub WebSocket server on 8765 and rendered live
Chinese captions inside CEF — so `overlay/index.html` needs **no changes** to
serve as the OBS caption layer, only the CSS override in the source settings.

---

## H-305 — replaces `virtual_cam.py`/pyvirtualcam. CONFIRMED

End-to-end, driven entirely by `laolao-vcam-setup.ps1` with no manual OBS
interaction (OBS launched `--minimize-to-tray --startvirtualcam`, webcam selected
over obs-websocket):

```
==> Registering the virtual camera filter for -Arch x64 (per-user, no admin)
    registered x64 filter: obs-virtualcam-module64.dll
==> Starting OBS (minimised to tray) with the virtual camera
    OBS running (pid 6256)
==> Selecting a webcam
    camera: ASUS FHD webcam
    virtual camera is RUNNING
```

OBS log:

```
Virtual output started
==== Virtual Camera Start ==========================================
Starting Virtual Camera output to Program
```

Then, from the **x64** ffmpeg (WeChat/Zoom architecture):

```
ffmpeg -f dshow -i video="OBS Virtual Camera" -frames:v 90 -update 1 out.png
  Stream #0:0: Video: rawvideo (NV12 / 0x3231564E), nv12, 1280x720, 30 fps
  frame=90 ... video:89339KiB
```

The captured frame is the live webcam with the caption
「你好，奶奶！我今天很好。」 composited over it —
`WS-C-evidence-vcam-x64-frame.jpg` in this folder.

That covers **A7** (webcam + captions → virtual camera pixels) and the
enumeration half of **A8**; the remaining A8 step is a human picking
"OBS Virtual Camera" inside real WeChat/Zoom.

### Config gotcha: `virtual-camera.type2`

In the scene collection, `type2` selects what the virtual camera outputs.
**`3` = Program** and is OBS's own default. Setting `1` selects *SceneOutput*,
which needs an extra `"scene"` name; without it OBS logs
`Starting Virtual Camera output to Scene :` and the camera streams a valid
1280×720 30fps NV12 signal that is **entirely black**. A black virtual camera
with a healthy-looking stream is this misconfiguration, not a driver problem.

---

## What this means for the port

- **`virtual_cam.py` / `pyvirtualcam` are not needed on ARM64** (H-002 REFUTED
  stands, and is now irrelevant). OBS does the compositing and owns the camera.
- The Electron `capturePage()` → JPEG → TCP :8766 sink is also bypassed. Only
  `server.py` (captions over WebSocket :8765) is required — which is what WS-A/WS-B
  are delivering. The overlay is consumed directly by OBS's CEF.
- **No admin, no installer, no `%APPDATA%` footprint.** Uninstall is
  `-Unregister` plus deleting the OBS folder.
- `main.js`'s Windows OBS detection (registry `HKLM\SOFTWARE\OBS Studio`, then
  prepending the OBS bin dir to PATH for pyvirtualcam) does **not** apply to a
  portable ARM64 OBS — there is no such registry key. Any Electron integration
  should shell out to `laolao-vcam-setup.ps1` instead.
- **Open risk:** if the user's WeChat/Zoom build turns out to be ARM64-native
  rather than x64, they must re-run with `-Arch arm64`. Worth verifying against
  the real apps before shipping; the script makes it a one-liner either way.

## Reproducing / cleaning up

```powershell
# set everything up and start the camera
.\docs\snapdragon\findings\laolao-vcam-setup.ps1

# if the call app can't open the camera, flip the architecture
.\docs\snapdragon\findings\laolao-vcam-setup.ps1 -Arch arm64

.\docs\snapdragon\findings\laolao-vcam-setup.ps1 -Stop         # stop OBS
.\docs\snapdragon\findings\laolao-vcam-setup.ps1 -Unregister   # remove the camera
```

Machine state left behind by this workstream: OBS portable + probes under
`C:\Users\snapd\Downloads\laolao-tools\obs-arm64\` (outside the repo), and the
per-user camera registration in `HKCU\SOFTWARE\Classes\CLSID`. The HKLM 64-bit
registrations created during testing were removed.

### The 32-bit (x86) filter — a free third seat, left registered

`C:\Windows\SysWOW64\regsvr32.exe /i /s obs-virtualcam-module32.dll` succeeded and
its keys live in `HKLM\SOFTWARE\Classes\WOW6432Node\CLSID`, a **separate registry
view** from the 64-bit slot — so it does not compete with the ARM64/x64 choice
above and all three can be registered at once. This is left in place deliberately:
some WeChat for Windows builds are 32-bit x86, and if the user's is, that view is
the one their app reads.

**Not verified** — no 32-bit DirectShow consumer was available to bind-test it
(BtbN ships no win32 ffmpeg build), so it is not part of `laolao-vcam-setup.ps1`.
Treat "32-bit apps work" as plausible-but-untested. To remove it:
`C:\Windows\SysWOW64\regsvr32.exe /u /s obs-virtualcam-module32.dll` (needs admin,
since it writes HKLM).
