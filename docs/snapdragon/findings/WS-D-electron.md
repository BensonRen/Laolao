# WS-D — Node.js + Electron shell on Windows ARM64

Owner: WS-D · Date: 2026-08-09/10 · Machine: Snapdragon X2 Elite, Win11 build 28000, ARM64

**Headline:** the Electron half of Laolao is fully sound on native ARM64. Node, npm,
Electron and `electron-builder` all have real ARM64 support, both windows open, and the
`capturePage() → JPEG → TCP :8766` frame pipeline works — proven all the way out of the
OS camera device. What is broken is not the compositor: it is (a) a hardcoded single
`venv/` interpreter, (b) registry-only OBS detection that also *gates the whole frame
pipeline*, and (c) a window-sizing bug that silently crops the captions off the frame.

---

## Hypothesis resolutions

| ID | Hypothesis | Result |
|---|---|---|
| H-400 | Node.js ships a Windows ARM64 build that runs here | **CONFIRMED** |
| H-401 | `npm install` in `electron/` resolves and pulls an arm64 Electron | **CONFIRMED** |
| H-402 | Electron launches win32-arm64 and opens both windows | **CONFIRMED (with a blocking-dialog caveat)** |
| H-403 | `capturePage() → JPEG → TCP :8766` frames genuinely arrive at a listener | **CONFIRMED — and extended to full end-to-end out of "OBS Virtual Camera"** |

---

## H-400 — Node.js on ARM64: CONFIRMED

`https://nodejs.org/dist/index.json` lists `win-arm64-zip` for the current LTS.

```
> $j = (iwr https://nodejs.org/dist/index.json).Content | ConvertFrom-Json
> ($j | ? { $_.lts -ne $false } | select -First 1).files
... win-arm64-7z, win-arm64-zip, win-x64-7z, win-x64-exe, win-x64-msi, win-x64-zip
```

Exact install that worked (portable, no admin, no winget):

```powershell
Invoke-WebRequest -UseBasicParsing `
  -Uri  "https://nodejs.org/dist/v24.19.0/node-v24.19.0-win-arm64.zip" `
  -OutFile "C:\Users\snapd\Downloads\laolao-tools\node-v24.19.0-win-arm64.zip"     # 33,463,079 bytes, 25.8 s
Expand-Archive ... ; Copy-Item node-v24.19.0-win-arm64\* C:\Users\snapd\Downloads\laolao-tools\node-arm64\ -Recurse
$env:PATH = "C:\Users\snapd\Downloads\laolao-tools\node-arm64;" + $env:PATH
```

Evidence:

```
> node -v
v24.19.0
> node -p "process.arch + ' ' + process.platform + ' ' + process.version"
arm64 win32 v24.19.0
> npm -v
11.17.0
```

**Native ARM64, not emulated.** Node lives at
`C:\Users\snapd\Downloads\laolao-tools\node-arm64\` and is only ever put on `PATH`
inside a shell invocation — nothing global was modified.

---

## H-401 — `npm install` on ARM64: CONFIRMED

```powershell
$env:PATH = "C:\Users\snapd\Downloads\laolao-tools\node-arm64;" + $env:PATH
cd C:\Users\snapd\Downloads\laolao\electron
npm install --no-audit --no-fund
```

```
added 310 packages in 35s
npm warn allow-scripts 1 package has install scripts not yet covered by allowScripts:
npm warn allow-scripts   electron@29.4.6 (postinstall: node install.js)
```

Only deprecation warnings (`inflight`, `glob`, `boolean`, `tar`) — **no build failures, no
node-gyp, no native modules**. The tree is pure JS, which is why ARM64 is a non-event.

The Electron binary that landed is genuinely ARM64 (PE `Machine` field read directly):

```
> Get-Content node_modules\electron\dist\version
29.4.6
> # PE header of node_modules\electron\dist\electron.exe
PE sig: 0x4550
Machine: 0xAA64      -> ARM64
```

**Installed: Electron 29.4.6, win32-arm64.**

⚠️ **npm 11.17 gotcha:** npm now withholds lifecycle scripts behind `allow-scripts`. Here
the Electron postinstall still ran (`dist/` and `path.txt` are present and correct), but on
a stricter npm/config it would not, leaving `node_modules/electron` with no binary. A
setup script for grandma should verify `node_modules/electron/dist/electron.exe` exists and
run `node node_modules/electron/install.js` if it does not.

---

## H-402 — Electron launches and opens both windows: CONFIRMED, with a caveat

Command: `npm start` in `electron/`, unmodified repo.

Both `BrowserWindow`s are created and the overlay loads in each:

```
[startup] engine root: C:\Users\snapd\Downloads\laolao
[diag] [CONTROL] overlay loaded
[diag] [OUTPUT ] overlay loaded
[diag] captureLoop: started
[diag] capturePage: first frame 1008x720
[diag] [CONTROL] preview: first output frame received (6158 bytes)
```

Native window enumeration, both owned by the Electron PID:

```
pid=20768 class=Chrome_WidgetWin_1 client=1008x740 title='Laolao Captions'
pid=20768 class=Chrome_WidgetWin_1 client=1008x720 title='Laolao Captions'
```

The capture loop then runs indefinitely at a real 30 fps (`frame #750` after ~41 s).

### Caveat 1 — the OBS dialog blocks the app before any window exists

`checkObs()` is `await`ed at `main.js:568`, *before* the windows are constructed at
`main.js:597` / `main.js:626`. With no OBS registry key the first run of `npm start`
produced **zero BrowserWindows** — only a modal `#32770` dialog titled
`OBS Studio Not Found`, and the app sat there forever:

```
pid=21736 class=#32770 client=540x235 title='OBS Studio Not Found'
(no Chrome_WidgetWin_1 windows at all)
```

Only after auto-dismissing that dialog did the two windows appear. For a non-technical
user (criterion A10) this reads as "the app doesn't start".

### Caveat 2 — the GPU process crashes on every launch (Adreno X2-90)

Reproducible on every single run:

```
[ERROR:command_buffer_proxy_impl.cc(323)] GPU state invalid after WaitForGetOffsetInRange.
[ERROR:gpu_process_host.cc(989)] GPU process exited unexpectedly: exit_code=34
```

It correlates with the first capture attempts returning nothing:

```
[diag] capturePage: first frame 0x0
[diag] capturePage: ZERO-SIZE frame (window not painting?)
```

Chromium then falls back to software compositing and everything works — 780+ good frames
afterwards — but this is Electron **29** (EOL) on a brand-new Adreno driver. Recommend
testing a current Electron (32+/latest) and, if it persists, shipping
`--use-angle=swiftshader` or `--disable-gpu-compositing` as a documented ARM64 fallback.

### Python children — verbatim failures

**Runs 1 and 2** (before any `venv/` existed in the repo root — the repo had only
`.venv-arm64` / `.venv-x64`):

```
[server] spawn error: spawn C:\Users\snapd\Downloads\laolao\venv\Scripts\python.exe ENOENT
[virtual_cam] spawn error: spawn C:\Users\snapd\Downloads\laolao\venv\Scripts\python.exe ENOENT
[server] exited (-1)
[server] restarting in 1000 ms…
...
[server] crashed 5× in a row — giving up.
```

`main.js:28` hardcodes `<root>/venv/Scripts/python.exe`; nothing in the ARM64 port creates
a plain `venv/`, so **both children die instantly on spawn** and the supervisor burns
through its 5-fast-crash budget in ~15 s and raises a "Caption engine failed repeatedly"
dialog.

**Run 3** — by then another workstream had created an emulated-x64 `venv/`
(`platform.machine() == AMD64`, Python 3.11.9), so the spawn succeeded and we got the real
runtime errors instead:

```
[virtual_cam] 00:07:16  INFO     Starting pyvirtualcam 0.15.0 (1280x720 @ 30 fps)
[virtual_cam] Traceback (most recent call last):
  File "C:\Users\snapd\Downloads\laolao\virtual_cam.py", line 96, in <module>
    main()
  File "C:\Users\snapd\Downloads\laolao\virtual_cam.py", line 78, in main
    with pyvirtualcam.Camera(width=WIDTH, height=HEIGHT, fps=FPS, fmt=pyvirtualcam.PixelFormat.RGB) as cam:
  File "C:\Users\snapd\Downloads\laolao\venv\Lib\site-packages\pyvirtualcam\camera.py", line 224, in __init__
    raise RuntimeError('\n'.join(errors))
RuntimeError: 'obs' backend: virtual camera output could not be started
'unitycapture' backend: No camera registered. Did you install any camera?
```

```
[server] 00:07:16  INFO     Laolao  model=small  lang=zh  device=mlx  vad=auto
[server] 00:07:16  INFO     Backend: faster-whisper (CPU int8)
[server] 00:07:27  INFO     faster-whisper ready.
[server] 00:07:28  INFO     SileroVAD ready (threshold=0.50)
[server] OSError: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8765):
         only one usage of each socket address (protocol/network address/port) is normally permitted
```

Both of those run-3 errors are **contention, not port bugs**: `:8765` was already held by
another workstream's caption server, and the OBS virtual camera already had a producer
(my own `virtual_cam.py`, see H-403). They are still worth recording because they show the
supervisor's behaviour is correct and that `virtual_cam.py` fails *loudly and identifiably*
when the OBS sink is unavailable.

On a bare ARM64 interpreter the sink is simply unavailable at all:

```
> .\venv\Scripts\python.exe -m pip install --only-binary=:all: pyvirtualcam
ERROR: Could not find a version that satisfies the requirement pyvirtualcam (from versions: none)
ERROR: No matching distribution found for pyvirtualcam
```

---

## H-403 — the frame pipeline: CONFIRMED (twice)

### Part 1 — frames genuinely arrive at a listener (no pyvirtualcam)

Stub receiver: **`docs/snapdragon/findings/ws_d_frame_sink.py`** (stdlib only — no numpy,
no Pillow, no pyvirtualcam; speaks `[4-byte BE length][JPEG]` exactly as
`virtual_cam.py:65-66` does).

Native ARM64 Electron connected to it and streamed continuously:

```
[sink] listening on 127.0.0.1:8766 (protocol: [4B BE len][JPEG])
[sink] client connected from ('127.0.0.1', 59110)
[sink] FIRST frame: 6158 bytes, magic=ffd8 tail=ffd9
[sink] 780 frames, 98884171 bytes total
```

...and from Electron's side:

```
[startup] virtual camera ready on :8766
[vcam] socket connected — frames now reach the virtual camera
[diag] capturePage: FIRST NON-BLACK frame at frame #136
[diag] capturePage: frame #750 1008x720 black=false vcSocket=true control=true
```

**780 frames / 98.9 MB in ~28 s ≈ 28 fps sustained** against a 30 fps target — the
localhost socket keeps up on ARM64 with room to spare.

Saved JPEGs verified with Pillow 12.3.0 (ARM64), in
`C:\Users\snapd\Downloads\laolao-tools\ws-d\frames\`:

| file | bytes | format | size | mean | stdev | min/max | % px > 16 | SOI/EOI |
|---|---|---|---|---|---|---|---|---|
| `frame_00001.jpg` | 6,158 | JPEG RGB | **1280x720** | 0.00 | 0.00 | 0/0 | 0.0% | ffd8/ffd9 |
| `frame_00046.jpg` | 6,158 | JPEG RGB | **1280x720** | 0.00 | 0.00 | 0/0 | 0.0% | ffd8/ffd9 |
| `frame_00091.jpg` | 6,158 | JPEG RGB | **1280x720** | 0.00 | 0.00 | 0/0 | 0.0% | ffd8/ffd9 |
| `frame_00136.jpg` | 156,198 | JPEG RGB | **1280x720** | **84.99** | 63.56 | 0/249 | **92.05%** | ffd8/ffd9 |
| `frame_00181.jpg` | 154,140 | JPEG RGB | **1280x720** | **79.21** | 60.85 | 0/255 | **94.33%** | ffd8/ffd9 |
| `frame_00226.jpg` | 153,854 | JPEG RGB | **1280x720** | **78.78** | 60.94 | 0/255 | **94.25%** | ffd8/ffd9 |

The three 6,158-byte frames are legitimately black and are *the expected result*: they were
captured before a camera had been chosen, which the app itself reports
(`[OUTPUT ] outputStartCamera: NO saved camera id — waiting for the control window to pick one`).
The moment a camera is selected the frames become real imagery — mean brightness ~79–85 out
of 255, ~94% of pixels above the black floor, 25x the byte size. Frame #136 is the exact
transition (`capturePage: FIRST NON-BLACK frame at frame #136`).

Visual confirmation of `frame_00181.jpg`: live webcam view **plus a composited caption bar**
rendering `capturePage -> JPEG -> TCP 8766` in white on translucent black. Both halves of
the compositor — video and text — survive the pipeline.

Camera selection was driven headlessly over the Chrome DevTools Protocol (no human
clicking), doing exactly what the picker's Start button does at `overlay/index.html:1352-1354`:

```
CAMERAS: [{"id":"a3717bbc…","label":"ASUS FHD webcam"},{"id":"8b76f7a8…","label":"OBS Virtual Camera"}]
STORE: stored
OUTPUT WINDOW STATE: {"hasStream":true,"readyState":4,"videoWidth":1280,"videoHeight":720,
  "trackLabel":"ASUS FHD webcam","trackState":"live","settings":{...,"frameRate":30,"width":1280,"height":720}}
```

`getUserMedia` on the hidden, `opacity:0`, always-on-top output window **works on Windows
ARM64** — the `paintWhenInitiallyHidden`/background-throttling strategy from
`CLAUDE.md` holds here (`black=false` sustained for 750+ frames). That was flagged as
"verified on macOS; re-verify on Windows" — consider it re-verified.

### Part 2 — full end-to-end out of the real OS camera device

Per the orchestrator's cross-workstream finding (OBS's ARM64 distribution ships the
DirectShow filter as x64/x86 only), the target architecture is a process split:

```
native ARM64 Electron --capturePage/JPEG--> TCP :8766 --> emulated x64 virtual_cam.py
    --> OBS DirectShow filter --> consumer
```

I ran exactly that, with the **unmodified** `virtual_cam.py` on WS-B's emulated x64
interpreter (run-only; nothing installed or modified in `.venv-x64`):

```
C:\Users\snapd\Downloads\laolao\.venv-x64\Scripts\python.exe virtual_cam.py 1280 720 30
  -> machine AMD64, py 3.11.9, pyvirtualcam 0.15.0

00:07:08  INFO  Starting pyvirtualcam 0.15.0 (1280x720 @ 30 fps)
00:07:08  INFO  Virtual camera ready — "OBS Virtual Camera" now appears in Zoom / FaceTime / WeChat
00:07:08  INFO  Frame server listening on 127.0.0.1:8766
00:07:16  INFO  Electron connected — streaming frames to virtual camera
00:07:44  INFO  Electron disconnected
```

Then read the camera back from a **separate emulated x64 consumer** via DirectShow
(`C:\Users\snapd\Downloads\laolao-tools\ws-d\dshow_consumer.py`, OpenCV 5.0.0 x64):

```
[consumer] DirectShow video inputs: ['ASUS FHD webcam', 'OBS Virtual Camera']
[consumer] opening device index 1 (OBS Virtual Camera) via CAP_DSHOW
[consumer] negotiated 1280.0x720.0
[consumer] saved camout_0030.jpg shape=(720, 1280, 3) mean=94.84 std=66.68 min=0 max=255
[consumer] saved camout_0040.jpg shape=(720, 1280, 3) mean=94.80 std=66.67 min=0 max=255
[consumer] saved camout_0050.jpg shape=(720, 1280, 3) mean=94.78 std=66.64 min=0 max=255
[consumer] saved camout_0060.jpg shape=(720, 1280, 3) mean=94.80 std=66.66 min=0 max=255
[consumer] DONE reads_ok=60 reads_failed=0 saved=4
```

Files in `C:\Users\snapd\Downloads\laolao-tools\ws-d\camout\` — 4 × ~236 KB, all
**1280x720x3**, mean brightness ~94.8, stdev ~66.7, **zero failed reads out of 60**.

Visual confirmation of `camout_0050.jpg`: live webcam imagery with the caption
**「你好，奶奶！我今天很好。」** ("Hello, Grandma! I'm doing well today.") rendered in the
caption bar.

**That caption was not injected by me and is not hardcoded anywhere in the repo**
(`grep -rn "奶奶" overlay/ electron/` → no matches). It arrived over the WebSocket from a
live caption server another workstream had running on `:8765`
(`[startup] caption server ready on :8765`; port owner PID 20968 = native ARM64
`Python311-arm64\python.exe`). So this frame incidentally demonstrates the whole product
path on Snapdragon: **ARM64 caption engine → WebSocket → ARM64 Electron overlay compositor
→ TCP → emulated x64 pyvirtualcam → OBS Virtual Camera → an app's camera picker.**

Caveat on ownership: I did not control that caption server, so I claim only that a caption
it produced survived my pipeline intact. Criterion A6/A7 belong to WS-A/WS-C/WS-E to claim.

**Conclusion: the Electron compositing half is sound on ARM64, and the process boundary at
TCP :8766 is a free architecture boundary — exactly as the orchestrator predicted.**

### Test harness disclosure

OBS is **not** installed in `Program Files` and writes no registry key here (it is WS-C's
portable install at `laolao-tools\obs-arm64\`). To get past `main.js`'s OBS gate without
editing the repo, I put a `reg.bat` shim ahead of `System32` on `PATH` for the Electron
process only. It intercepts *only* the `HKLM\SOFTWARE\OBS Studio` query and forwards
everything else to the real `reg.exe`:

```
C:\Users\snapd\Downloads\laolao-tools\ws-d\shim\reg.bat
  -> echoes "(Default) REG_SZ C:\Users\snapd\Downloads\laolao-tools\obs-arm64"
```

This is a *diagnostic shim, not a fix* — it exists to prove the pipeline downstream of the
gate. The real fix is inventory item **#5/#6** below. No repo file was modified.

---

## Inventory: x86-64 / OBS-specific assumptions

### `electron/main.js`

**#1 — `main.js:27-31` — single hardcoded `venv/` interpreter for both children**

```js
function venvPyFor(root) {
  return IS_WIN ? path.join(root, 'venv', 'Scripts', 'python.exe') : ...;
}
```

Two problems. It assumes the venv is literally named `venv`, and it assumes **one**
interpreter serves both children. On ARM64 that is now provably wrong: `server.py` wants
the native ARM64 interpreter (ONNX/QNN lane) while `virtual_cam.py` **must** be emulated
x64, because the OBS DirectShow filter is an in-process x64 DLL.

*Fix:* split the resolver and make both overridable.

```js
function pyFor(root, key, fallbackDir) {
  return process.env[key.env]
      || readSettings()[key.settings]
      || path.join(root, fallbackDir, 'Scripts', 'python.exe');
}
const ENGINE_PY = pyFor(root, {env:'LAOLAO_PY',     settings:'pythonEngine'}, 'venv');
const CAMERA_PY = pyFor(root, {env:'LAOLAO_CAM_PY', settings:'pythonCamera'}, 'venv');
```

Defaults stay `venv/` so macOS and x64 Windows are unaffected; the ARM64 installer writes
`{"pythonEngine": "<root>\\.venv-arm64\\Scripts\\python.exe",
"pythonCamera": "<root>\\.venv-x64\\Scripts\\python.exe"}` into `<userData>/settings.json`.
`spawnPython()` (**#7**) then takes the interpreter as a parameter instead of closing over
the single global.

**#2 — `main.js:33-37` — `rootValid()` validates only the one venv**

```js
return !!root && fs.existsSync(path.join(root,'server.py')) && fs.existsSync(venvPyFor(root));
```

With the split above, a perfectly good ARM64 checkout (`.venv-arm64` + `.venv-x64`, no
`venv/`) is rejected and the packaged app throws the "Setup Needed" recovery dialog.
*Fix:* validate `ENGINE_PY` (required) and warn-but-continue on a missing `CAMERA_PY`
(captions-only is a legitimate degraded mode).

**#3 — `main.js:83-87` — recovery-dialog text is macOS/POSIX**

Tells the user to `git clone … ~/code/Laolao` and run `./setup.sh`. On Windows the script
is `setup.bat` and the path renders as `C:\Users\<u>\code\Laolao`. Cosmetic, but it is the
one screen a stuck non-technical user reads (criterion A10). *Fix:* branch the text on
`IS_WIN`.

**#4 — `main.js:71` — `~/code/Laolao` default root**

Harmless on Windows (`app.getPath('home')` resolves), just an unlikely location. Keep, but
put `LAOLAO_ROOT` first in the docs.

**#5 — `main.js:172-185` — `findObsWinBin()` is registry-only and x64-shaped**

```js
const out = execSync('reg query "HKLM\\SOFTWARE\\OBS Studio" /ve', ...);
const bin = path.join(m[1].trim(), 'bin', '64bit');
...
const fallback = 'C:\\Program Files\\obs-studio\\bin\\64bit';
```

Verified failures on this machine:

```
> reg query "HKLM\SOFTWARE\OBS Studio" /ve
ERROR: The system was unable to find the specified registry key or value.     (exit 1)
> Test-Path "C:\Program Files\obs-studio\bin\64bit"
False
```

...while OBS **is** present and its virtual-camera filter **is** registered
(`Test-Path C:\Users\snapd\Downloads\laolao-tools\obs-arm64\bin\64bit` → `True`, and the
device enumerates as `OBS Virtual Camera` in both Chromium and DirectShow). A portable OBS
never writes that key, and on ARM64 Windows an emulated x64 OBS installs to
`C:\Program Files (x86)\obs-studio`, which is not checked either.

Two smaller notes: `execSync` inherits stderr, so the failed `reg query` leaks
`ERROR: The system was unable to find…` into the app's stderr on every OBS-less launch;
and the literal `bin\64bit` reads as "x64" but is in fact also the layout of the ARM64 zip,
so the name survives — worth a comment so nobody "fixes" it to `bin\arm64`.

*Fix:* a candidate ladder, and stop treating "OBS the app" as the thing to detect —

```js
function findObsWinBin() {
  const cands = [
    process.env.LAOLAO_OBS_BIN,
    readSettings().obsBin,
    regDefault('HKLM\\SOFTWARE\\OBS Studio'),          // stdio:'pipe' — don't leak stderr
    path.join(process.env['ProgramFiles']      || '', 'obs-studio', 'bin', '64bit'),
    path.join(process.env['ProgramFiles(x86)'] || '', 'obs-studio', 'bin', '64bit'),
    path.join(process.env['LOCALAPPDATA']      || '', 'Programs', 'obs-studio', 'bin', '64bit'),
  ];
  return cands.find(c => c && fs.existsSync(c)) || null;
}
```

and detect the **filter**, not the app, since that is what actually determines whether a
camera can exist:

```
HKCR\CLSID\{A3FCE0F5-3493-419F-958A-ABA1250EC20B}\InprocServer32
HKCR\WOW6432Node\CLSID\{A3FCE0F5-3493-419F-958A-ABA1250EC20B}\InprocServer32
```

Present on this machine with no OBS "installation" at all.

**#6 — `main.js:187-211` + `:568` + `:579-589` + `:656-661` — OBS presence gates far too much**

Two distinct defects.

*6a — it blocks window creation.* `const obsAvailable = await checkObs();` at `:568` runs
before `new BrowserWindow(...)` at `:597`/`:626`. Verified: with no OBS key, `npm start`
created **zero windows** and hung on the dialog indefinitely.
*Fix:* create both windows first, start the capture loop, and run `checkObs()` afterwards;
show its dialog non-blocking (or as a banner in the control window).

*6b — it gates the frame pipeline itself.* 

```js
if (obsAvailable) { vcamSup = supervise('virtual_cam', ...); } else { /* captions-only */ }
...
if (obsAvailable) { waitForPort(VCAM_PORT, ...).then(() => connectVcSocket()); }
```

So when OBS isn't *detected*, main.js not only skips spawning the sink — it also **never
connects to :8766 at all**, so a perfectly healthy sink already listening there is ignored.
Verified in run 1: `[vcam] OBS not found — running captions-only`, and
`vcSocket=false` for 750+ consecutive frames while frames were being produced fine.

This matters a lot for the ARM64 port, where the sink is deliberately an out-of-process,
differently-architected helper that may well be started by something other than main.js.

*Fix:* decouple. `connectVcSocket()` already retries forever and is harmless when nothing
is listening, so run it **unconditionally**. Let `obsAvailable` gate only (i) whether we
spawn our own `virtual_cam.py` and (ii) whether we nag the user.

**#7 — `main.js:216-234` — `spawnPython()` PATH manipulation and interpreter**

```js
const env = { ...process.env };
if (IS_WIN) {
  const obsBin = obsWinBin || 'C:\\Program Files\\obs-studio\\bin\\64bit';
  env.PATH = `${obsBin};${env.PATH || ''}`;
}
const proc = spawn(VENV_PY, [script, ...args], { cwd: ROOT, stdio: [...], env });
```

Three issues: the hardcoded `C:\Program Files\obs-studio\bin\64bit` fallback is duplicated
from `:183` (same wrong assumption, second copy); the OBS bin dir is prepended for **every**
child including `server.py`, which never needs it; and on ARM64 it would push *ARM64* OBS
DLLs onto the PATH of a process that must be *x64*. *Fix:* make the interpreter and the
PATH prepend per-child — only the camera child gets an OBS bin dir, and it should be the
one matching that child's architecture.

**#8 — `main.js:151-152` + `:626-644` + `:450-476` — the output window is silently
cropped, and it eats the captions**

This is the one genuinely new ARM64-machine defect and it is the highest-severity item
after the venv split.

`CAM_W/CAM_H = 1280x720`, and the output window is created `useContentSize: true,
width: 1280, height: 720` with the default frame. On this machine the desktop's DIP work
area is **1024x768**, so Chromium clamps the window at creation and the content area comes
out **1008x720** (1024 minus 16 px of window border). Measured three independent ways:

```
[diag] capturePage: first frame 1008x720
OUTPUT geometry (from the renderer): {"iw":1008,"ih":720,"dpr":1}
probe: contentSize=[1008,720] capturePage=1008x720
DISPLAY bounds={"x":0,"y":0,"width":1024,"height":768} workArea={...} scaleFactor=1
```

`capturePage()` therefore returns a 1.40 aspect image, which trips the "aspect-safe"
center-crop at `:466-475` — designed as a no-op safety net, here doing real damage:

- src 1008x720 (1.400) vs target 1.778 → crop height to `1008/1.778 = 567`
- **top and bottom 76 px each (21% of the frame height) are discarded**
- the 1008x567 remainder is upscaled to 1280x720 at `:476`

The caption bar lives at the bottom of the frame, so **the captions get clipped** — visible
in `frame_00181.jpg`, where the caption's descenders are cut by the frame edge. Captions
being clipped is a product-defining failure, and it is completely silent: `black=false`,
frames flow, the log says 1280x720 at the sink.

*Fix (verified by experiment).* Calling `setContentSize()` **after** construction bypasses
Chromium's creation-time clamp. Probe results on this 1024x768 desktop:

| variant | contentSize | capturePage | result |
|---|---|---|---|
| A baseline (main.js as-is) | [1008,720] | 1008x720 | FAIL |
| B `frame:false` | [1024,720] | 1024x720 | FAIL |
| C `frame:false` + `minWidth/minHeight` | [1024,720] | 1024x720 | FAIL |
| D `frame:false` + `setContentSize()` after | [1280,720] | **1280x720** | **PASS** |
| E `frame:false` + `setBounds()` after | [1280,720] | **1280x720** | **PASS** |
| F `offscreen: true` (`show:false`) | [1024,720] | 1024x720 | FAIL |
| **G baseline + `setContentSize()` after** | [1280,720] | **1280x720** | **PASS** |
| H `resizable:true` + `setContentSize()` after | [1280,720] | **1280x720** | **PASS** |

Variant **G is a one-line, zero-risk change** — no other window option needs to move:

```js
outputWindow.setIgnoreMouseEvents(true);
outputWindow.setContentSize(CAM_W, CAM_H);   // ADD: creation-time bounds are clamped to
                                             // the work area on small/scaled displays
```

Note `offscreen: true` does **not** help (F) — it is clamped too — so the obvious
alternative is a dead end. Worth adding a startup assertion: if
`capturePage().getSize()` ≠ `CAM_W x CAM_H`, log loudly rather than silently cropping.

**#9 — `main.js:15` — mac-only Chromium switch, and it clobbers the list**

```js
app.commandLine.appendSwitch('disable-features', 'MacWebContentsOcclusion');
```

Harmless on Windows, but `disable-features` is a single comma-joined list — anything else
that needs disabling (a likely ARM64 GPU workaround, see H-402 caveat 2) must be appended
to this same string, not added as a second `appendSwitch`. Flagging before someone hits it.

**#10 — `main.js:495-500` — no socket backpressure**

```js
vcSocket.write(header); vcSocket.write(jpeg);
```

Return values ignored. Measured throughput was fine (780 frames / 98.9 MB / ~28 s ≈
3.5 MB/s, 28 fps), so this is not a blocker — but the ARM64 producer now feeds an
*emulated x64* consumer, which is the slower side of the pipe by construction. If it ever
falls behind, the write queue grows without bound. *Fix:* skip a frame when
`vcSocket.write()` returns `false` (drop, don't buffer — it's live video).

**#11 — `main.js:161-170` `findObsMac()` / `:550-559` `askForMediaAccess`** — correctly
`IS_MAC`-guarded; no ARM64 Windows impact. Camera/mic permission on Windows worked with no
prompt at all (`setPermissionRequestHandler` at `:540-542` is sufficient).

### `electron/package.json`

**#12 — `package.json:39-45` — `npm run build:win` does NOT target arm64**

```json
"win": { "target": [ { "target": "nsis",     "arch": ["x64"] },
                     { "target": "portable", "arch": ["x64"] } ] }
```

Answering the question directly: **no.** `npm run build:win` produces x64-only artifacts,
which on this machine would run the entire Electron shell under Prism emulation —
needlessly, since everything about the shell is ARM64-clean.

electron-builder handles arm64 fine; verified by building it:

```
> npx electron-builder --win --arm64 --dir
  • electron-builder  version=24.13.3 os=10.0.28000
  • packaging  platform=win32 arch=arm64 electron=29.4.6 appOutDir=...\dist\win-arm64-unpacked
  • downloading  url=.../v29.4.6/electron-v29.4.6-win32-arm64.zip size=110 MB
  (exit code 0)

> # PE header of dist\win-arm64-unpacked\Laolao.exe
Laolao.exe machine=0xAA64 (ARM64)      166,069,248 bytes
```

*Fix:*

```json
"win": { "target": [ { "target": "nsis",     "arch": ["x64", "arm64"] },
                     { "target": "portable", "arch": ["x64", "arm64"] } ] }
```

NSIS will then emit a per-arch installer (or a universal one with `"nsis": {...}` unchanged).

**#13 — `package.json:14` — `"electron": "^29.0.0"`**

Resolves to 29.4.6, which does ship win32-arm64 (confirmed above). But Electron 29 is
end-of-life and its GPU process crashes on this Adreno driver every launch (H-402 caveat 2).
Recommend bumping to a supported major and re-checking the GPU log.

**#14 — `package.json:36-38` — `extraResources`**

```json
"extraResources": [ { "from": "resources/", "to": "resources/", "filter": ["**/*"] } ]
```

`electron/resources/` contains exactly one file, `PLUGIN_README.md`, and `main.js` never
reads `process.resourcesPath`. Nothing arch-specific; no action. (The `CLAUDE.md` warning
about `process.resourcesPath` vs `__dirname` is currently moot.)

**#15 — `package.json:2-5`** — no `author` field; electron-builder warns
(`author is missed in the package.json`). Cosmetic, but NSIS metadata uses it.

---

## Recommended change set, in priority order

1. **`setContentSize(CAM_W, CAM_H)` after creating the output window** (`main.js:645`) —
   one line, fixes silently-clipped captions. Verified.
2. **Split the Python interpreter resolution** — engine on ARM64, camera sink on x64
   (`main.js:27-31`, `:216-234`, `:572-586`).
3. **Un-gate the frame pipeline from OBS detection** — always run `connectVcSocket()`
   (`main.js:656-661`).
4. **Move `checkObs()` after window creation** and make its dialog non-blocking
   (`main.js:568`).
5. **Replace registry-only OBS detection** with a candidate ladder + DirectShow-filter
   CLSID probe (`main.js:172-185`, `:221`).
6. **Add `arm64` to the Windows build targets** (`package.json:41-44`).
7. Bump Electron off 29; investigate the Adreno GPU-process crash.
8. Drop frames instead of buffering when the vcam socket applies backpressure
   (`main.js:495-500`).

---

## Artifacts

| Path | What |
|---|---|
| `docs/snapdragon/findings/ws_d_frame_sink.py` | **Deliverable.** Stdlib-only TCP :8766 `[4B BE len][JPEG]` receiver; saves frames + JSON summary. Reusable by WS-E. |
| `C:\Users\snapd\Downloads\laolao-tools\node-arm64\` | Portable Node v24.19.0 win-arm64 (WS-D-owned) |
| `C:\Users\snapd\Downloads\laolao-tools\ws-d\frames\` | 6 JPEGs from the stub-sink run (H-403 part 1) |
| `C:\Users\snapd\Downloads\laolao-tools\ws-d\camout\` | 4 JPEGs read back out of "OBS Virtual Camera" (H-403 part 2) |
| `C:\Users\snapd\Downloads\laolao-tools\ws-d\dshow_consumer.py` | x64 DirectShow consumer used for the end-to-end read-back |
| `C:\Users\snapd\Downloads\laolao-tools\ws-d\shim\reg.bat` | Diagnostic OBS-detection shim (test harness only — not a fix) |
| `C:\Users\snapd\Downloads\laolao-tools\ws-d\venv\` | WS-D ARM64 venv (Pillow 12.3.0) for image verification |
| `C:\Users\snapd\Downloads\laolao-tools\ws-d\venv-x64\` | WS-D x64 venv (OpenCV 5.0.0, pygrabber) for the consumer |
| `C:\Users\snapd\Downloads\laolao\dist\win-arm64-unpacked\` | Proof-of-concept ARM64 package (`Laolao.exe`, machine=0xAA64) |

Isolation respected: `.venv-arm64` and `.venv-x64` were never modified — `.venv-x64`'s
interpreter was only *run*. No existing repo file was edited. All Electron/node/python
processes started by WS-D were terminated (`remaining electron: 0`, `:8766` free).
