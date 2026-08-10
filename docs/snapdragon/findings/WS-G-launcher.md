# WS-G — One-command launch (A10)

Owner: WS-G · Machine: Snapdragon X2 Elite (X2E88100), Windows 11 build 28000, ARM64
Date: 2026-08-10

**A10 passes, and it passes against a check that can no longer be fooled.**
`Laolao-arm64.bat` takes a Snapdragon machine from "nothing running" to
"WeChat can pick a camera with live Chinese captions on it" in **6 seconds
warm**, with no administrator rights, no installer, and no terminal. Running it
twice is a no-op. `Laolao-stop.bat` puts everything back.

The chosen camera path is **OBS as the compositor** (WS-C's lane), not Electron
+ emulated pyvirtualcam (WS-D's lane). The reasoning is below and it is not a
preference — the Electron path cannot be shipped to a non-technical user
without editing `electron/main.js`, which is out of scope for this workstream
and has three separate known defects on this exact machine.

---

## What was built

| Path | What it is |
|---|---|
| `Laolao-arm64.bat` | **The thing you double-click.** Arch guard, then `launch.ps1`, then keeps the window open so the instructions can be read. |
| `Laolao-stop.bat` | The thing you double-click to stop. |
| `docs/snapdragon/launch.ps1` | The launcher proper: probe setup → engine → camera → caption window → plain-language instructions. Also `-Stop`, `-Status`, `-Arch`, `-NoBrowser`, `-NoCamera`, `-Setup`. |
| `docs/snapdragon/setup-arm64.ps1` | First-run setup for a clean machine: find ARM64 Python, build `.venv-arm64`, install `requirements-arm64.txt`, pre-download the NPU model, then delegate the camera to WS-C's `laolao-vcam-setup.ps1`. |
| `requirements-arm64.txt` | The dependency set that actually installs here, with the three impossible packages named and explained. |
| `docs/snapdragon/acceptance/check.py` — `a10()` | Rewritten. It now runs the launcher for real. See "The check was the weakest part". |

Nothing under `backends/`, `electron/`, `overlay/`, `server.py` or `config.json`
was modified. The launcher is additive.

---

## The user-facing steps, exactly

**First time, on a machine with nothing installed:**

1. Install Python (the only manual step). <https://www.python.org/downloads/windows/>
   → **"Windows installer (ARM64)"**, 3.11 or 3.12, tick *Add python.exe to PATH*.
2. Download / clone Laolao.
3. **Double-click `Laolao-arm64.bat`.**
   It says what it is doing and installs the rest itself: the Python packages,
   the speech model (~200 MB), and portable OBS ARM64 (~167 MB).
4. When it finishes it prints:

```
   1. Open WeChat / Zoom / Teams and go to its video settings.
   2. For the camera, choose:  OBS Virtual Camera
   3. Start speaking. Big Chinese subtitles appear on your video.

   Already had the call app open? Close it completely and reopen it -
   call apps only look for cameras when they start.
```

**Every time after that:** double-click `Laolao-arm64.bat`. ~6 seconds.
**To stop:** double-click `Laolao-stop.bat`.

**If the camera is in the list but the picture is black** — the one Snapdragon
trap, inherited from H-303 — run `Laolao-arm64.bat -Arch arm64`. The launcher
prints that line itself, every launch, so the recovery is on screen before the
problem is.

Why Python is still manual: silently running a python.org installer would
register a second 3.11 under `HKCU\Software\Python\PythonCore` and could
displace an existing one. Automating that is a way to break a machine, not to
fix one — and the README already asks Windows and macOS users to install
Python. The ARM64 path is strictly *less* manual than the shipped Windows path,
because OBS installs itself.

---

## Path choice: OBS composites, Electron does not

Both paths are proven end-to-end by other workstreams. The question A10 asks is
narrower: which one can a non-technical person get working?

| | **(a) OBS as compositor** (chosen) | **(b) Electron + emulated x64 sink** |
|---|---|---|
| Processes to get right | 2 (`server.py`, `obs64.exe`) | 4 (`server.py` ARM64, Electron ARM64, `virtual_cam.py` x64, OBS filter) |
| Python environments | 1 native ARM64 | **2** — plus an x64 Python install, which is a second manual download |
| Node/npm/Electron | not needed | needed: ~310 packages, and npm 11 withholds Electron's postinstall behind `allow-scripts` (WS-D H-401) |
| Blocking failure on a clean machine | none found | `main.js:28` hardcodes `<root>/venv/Scripts/python.exe`; the ARM64 port has no `venv/`, so **both children die on spawn** and the supervisor gives up in ~15 s (WS-D, verbatim) |
| Second blocking failure | — | `checkObs()` is awaited *before* the windows are created and OBS writes no registry key here, so the first run produces **zero windows** and a modal "OBS Studio Not Found" (WS-D H-402) |
| Third | — | on this 1024×768 desktop the output window is clamped to 1008×720, and the aspect-safe crop then throws away 21% of the frame height — **the captions get clipped** (WS-D #8) |
| Fixes required in `electron/main.js` | none | at least 4, and WS-G is explicitly barred from touching that file |
| Product UX | caption monitor window + OBS in the tray | real control window, toolbar, language switch, live preview |
| Echo cancellation | **no** — see the honest caveat below | yes (Chromium `getUserMedia`) |

Path (b) is the better product. It is not the shippable one *today*: three of
its four failure modes hit on the very first launch of a clean ARM64 machine,
all of them live in a file this workstream must not modify, and two of them
look to the user exactly like "the app is broken". Path (a) has one moving
part, no `pyvirtualcam`, no x64 Python, no Node, and WS-C already proved it
from a torn-down state.

**Path (b) stays documented as the alternative**, and it becomes the right
default the moment WS-D's items 1–4 land in `main.js`. Nothing in this
launcher blocks that: `launch.ps1` owns only the engine and the camera, so an
Electron shell can replace steps 3 and 4 without touching steps 1 and 2.

### The honest cost of choosing (a)

**No echo cancellation.** In path (a) the microphone is captured by
`sounddevice` inside `server.py`. Under Electron the mic is captured by
Chromium with `echoCancellation: true`, which is why the README says it
matters: without it, the far end's voice coming out of your speakers gets
transcribed and shown as *your* caption. On this path, **use a headset** — it
is now in the README's ARM64 section as a requirement, not a tip.

**No toolbar.** Language, colours and caption position are not switchable
mid-call; they come from `config.json` and URL parameters. `Laolao-arm64.bat`
opens the overlay in display-only mode (`?output=1`), which deliberately opens
neither the camera nor the microphone — so it can never fight OBS for the
webcam or double-feed audio to the engine.

---

## Evidence

### Cold start, from a genuinely torn-down state

Teardown first (`Laolao-stop.bat`), then the camera registration deleted from
`HKCU` to force the setup path:

```
==> [1/4] Checking that everything is installed
==> Looking for a native ARM64 Python 3.10+
    C:\Users\snapd\AppData\Local\Programs\Python\Python311-arm64\python.exe  (ARM64 3.11)
==> Python virtual environment (.venv-arm64)
    .venv-arm64 ready (ARM64 3.11)
==> Python packages
    onnxruntime-qnn, sounddevice, websockets, tokenizers, opencc, Pillow present
    skipped by design: silero-vad, faster-whisper, pyvirtualcam (no win-arm64 build)
==> Speech model for the Hexagon NPU
BACKEND_OK QnnWhisperBackend in 1.4s
==> [2/4] Starting the caption engine
    listening on port 8765 (pid 18300)
==> [3/4] Starting the camera
    registered x64 filter: obs-virtualcam-module64.dll
    OBS running (pid 23228)
    camera: ASUS FHD webcam
    virtual camera is RUNNING
==> [4/4] Opening the caption window

  ============================================================
   Laolao is running.
  ============================================================
```

`RETURNED rc=0 in 6.6s` measured from a Python parent, warm caches.

### The camera really has captions on it

Read back out of `OBS Virtual Camera` by an **x64** ffmpeg — the architecture
WeChat and Zoom run as — while the launcher-started stack was up:

```
Stream #0:0: Video: rawvideo (NV12), nv12, 1280x720, 30 fps
frame=45 ... 224,406-byte PNG
```

The frame is the live webcam with a Chinese caption block composited over it.

### The engine started by the launcher is transcribing

Streaming `tests/fixtures/chinese_speech.wav` at whatever the launcher left
running on `:8765`:

```
  partial  甚至出现交易
  partial  甚至出现交易几乎成绩的情况。
  final    甚至出现交易几乎停止的情况。
RESULT captions=3 finals=1
```

### The acceptance harness, run by itself

```
[PASS ] A10  one-command launch: the launcher actually brings Laolao up
  files present, PowerShell parses
  no elevation markers (harness itself elevated=True)
  cold: port free, OBS down
  launcher exit 0 in 6s
  engine pid=13560  engine lane = .venv-arm64 (native ARM64)
  captions=['甚至出现交易几乎停止的情况。', '甚至出现交易几乎停止的情况。']
  obs64 pid=18724
  camera filter {'OBS Virtual Camera': 'AMD64(x64)'}
  registration HKCU=True HKLM=False (per-user, no admin)
  second run reused the running engine and camera
  stopped cleanly
```

And the whole board, in one run of the harness:

```
[PASS ] A1 … [PASS ] A10
PASS=10  FAIL=0  SKIP=0  BLOCKED=0
```

### No administrator, argued from what is actually written

The camera lives entirely in `HKCU\SOFTWARE\Classes\CLSID` and `HKLM` has no
such key — `HKCU=True HKLM=False` above, asserted by the check, and the camera
demonstrably works in that state. Everything else is written under the repo and
`laolao-tools\`. No script contains `runas`, `-Verb RunAs` or a
`requestedExecutionLevel`; the check greps for all of them.

One honest gap: the harness process was itself elevated (`elevated=True`), so
"works unelevated" is argued from *where the writes land*, not observed from an
unelevated run. `runas /trustlevel:0x20000` to drop privileges failed on this
box (exit 1, no output), so it was not possible to demonstrate directly. The
check reports its own elevation state rather than pretending otherwise.

---

## The check was the weakest part, so it was replaced

The shipped `a10()` was:

```python
cands = [REPO/"run-arm64.bat", REPO/"Laolao-arm64.bat",
         REPO/"docs"/"snapdragon"/"launch.ps1", REPO/"run.bat"]
found = [p for p in cands if p.exists()]
...
return _r("A10", ..., PASS, f"launchers present: {[p.name for p in found]}")
```

It grades the *existence of a filename*. `echo. > Laolao-arm64.bat` passes it.
For the one criterion whose subject is a non-technical human, that is worth
nothing — so it was rewritten to run the launcher and judge the outcome from
outside it. **This was a deliberate change to the grading instrument; it makes
A10 strictly harder to pass.** What it now does:

1. all three artefacts exist, and both `.ps1` files **tokenise** (a launcher
   that cannot parse fails at double-click time with a wall of red)
2. no elevation markers anywhere in the launcher sources
3. tear the machine down with `-Stop`, and **assert it is actually cold** —
   port free, no `obs64`. A "cold start" measured on an already-running system
   proves nothing
4. run `Laolao-arm64.bat -NoBrowser` through `cmd /c`, the way Explorer would,
   and require exit 0
5. then verify from the outside, not from the launcher's own claims:
   - something is listening on the caption port, **and its process lineage goes
     back to `.venv-arm64`** (not a system interpreter that happens to work)
   - it is **transcribing** — a speech fixture is streamed at it and a caption
     must come back. A socket that binds is not a caption engine
   - `obs64` is running
   - a virtual camera is registered **and its DLL is `AMD64(x64)`**, i.e. the
     one WeChat/Zoom can actually `CoCreateInstance`. Visible-but-dead is the
     documented failure mode here (H-303), so it is graded explicitly
   - the registration is in `HKCU`, i.e. writable without admin
6. run the launcher **again** and require exit 0 with the *same* engine pid and
   the *same* OBS pid — idempotence, not just survival
7. `-Stop`, and require the port to be free afterwards

`LAOLAO_A10_STATIC=1` skips the live half; it then reports SKIP, never PASS.

### Negative control

The rewrite was tested against a deliberately fake launcher — one that parses,
prints "Laolao is running." and exits 0:

```
[FAIL ] A10  one-command launch: the launcher actually brings Laolao up
  FAILURES: nothing listening on 8765 after launch;
            OBS is not running — nothing is producing camera frames
```

The old check passed that stub. This one does not.

---

## Three bugs found while validating

**1. The launcher hung any script that captured its output — for 10 minutes.**
`Start-Process -RedirectStandardOutput` makes PowerShell create the child with
`bInheritHandles=TRUE`, so `server.py` also inherited the *launcher's* stdout
pipe. The launcher exited in 6 s; the caller then blocked on EOF until the
background server died. The acceptance harness hung on exactly this before
anything else was wrong. Fix: the engine is started through a generated
`run-engine.cmd` that does its own `>` redirection, launched with no
`-Redirect*` at all — `Start-Process` then goes through ShellExecute, which
inherits nothing. Worth knowing generally: *on Windows, "run a daemon and
return" and "capture the parent's output" are incompatible unless the daemon's
handles are severed.*

**2. Killing the pid you started does not kill the server.** A Windows venv's
`python.exe` is a stub that re-execs its base interpreter, and it is the
**child** that owns the socket. `Stop-Process -Id <saved pid>` left the real
server alive and holding `:8765`. Fix: `Stop-Tree` walks `Win32_Process` by
`ParentProcessId`. Same fact broke the check's "which interpreter is this?"
test, which is why `_port_owner()` now returns the parent's image path too.

**3. The caption window would not open if the user had Edge open.** Launching
`msedge --app=<overlay>` against the user's own profile aborts when that
profile is already locked:

```
ERROR:process_singleton_win.cc:868 Lock file can not be created! Error code: 32
Failed to create a ProcessSingleton for your profile directory ... Aborting now
```

No window, no error the launcher could see. Fix: the caption window gets its
own `--user-data-dir` under `laolao-tools\run\`, so it never contends with the
user's browser session (and never inherits their extensions or session
restore).

---

## Limits of this validation

- **The GUI half is inferred, not seen.** This agent's shell runs in **session
  0**; `EnumWindows` returns 0 visible windows there, so no launcher-opened
  window could be observed directly. What *was* observed: after the launcher's
  step 4, `server.err.log` records `connection open` from the Edge process —
  the caption window loaded `overlay/index.html`, ran, and connected to the
  caption engine. The same page renders captions correctly inside OBS's CEF,
  which is proven by the pixels in the virtual-camera frame. The window's
  *appearance* on a real desktop is the one thing here taken on inference.
- **No human has picked "OBS Virtual Camera" inside real WeChat or Zoom.** The
  x64 ffmpeg read-back is the closest available proxy (same architecture, same
  DirectShow path). That last step is still A8's open item.
- **Silence hallucination is gated but not eliminated.** With the local mic
  open in a quiet room the engine produced no captions for over a minute of
  logs — EnergyVAD holds. But a caption reading `你不想要` did appear once,
  when this agent streamed digital silence over the WebSocket *while the mic
  was also open* (the dual-source case `server.py` warns about, and a test
  artefact rather than a user path). WS-A documents the underlying behaviour:
  Whisper hallucinates on silence and the backend does not filter it. Do not
  remove the VAD gate or the chars-per-second cap on this lane.
- **`-Arch arm64` was re-tested through the launcher**, in both directions, and
  the arch-mismatch detection works: a second launch with the default `x64`
  noticed the registration no longer matched, reused the running engine, and
  rebuilt only the camera.

  ```
  before:              obs-virtualcam-module64.dll
  -Arch arm64      ->  registered arm64 filter: obs-virtualcam-module-arm64.dll
  (default, x64)   ->  already running (pid 21932) - reusing it
                       registered x64 filter: obs-virtualcam-module64.dll
  after:               obs-virtualcam-module64.dll
  ```

## Reproducing

```powershell
.\Laolao-arm64.bat                 # start (installs anything missing)
.\Laolao-arm64.bat -Status         # what is running
.\Laolao-arm64.bat -Arch arm64     # camera for ARM64-native call apps
.\Laolao-stop.bat                  # stop

# grade it
.venv-arm64\Scripts\python.exe docs\snapdragon\acceptance\check.py --only A10
```

Machine state left behind: nothing new. Setup writes `.venv-arm64` (already
present), `laolao-tools\obs-arm64\`, `laolao-tools\models\`,
`laolao-tools\run\` (pid file, logs, the generated `run-engine.cmd`, the
caption window's Edge profile), and the per-user camera registration in
`HKCU\SOFTWARE\Classes\CLSID`. Uninstall is `Laolao-stop.bat`,
`laolao-vcam-setup.ps1 -Unregister`, and deleting `laolao-tools\`.
