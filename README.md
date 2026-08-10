# 老老 Laolao — Real-time Speech Captions for Video Calls

**Fully offline, open source, Chinese-first live captioning — double-click to launch, no OBS required.**

Built for families calling elderly relatives with hearing difficulty. Speak naturally; giant subtitles appear on your camera feed in real time. Works with WeChat, Zoom, FaceTime, WhatsApp, and any other video call app.

```
You speak  →  local Whisper  →  captions overlay  →  virtual camera
                (on-device)        (Electron app)       (Zoom sees it)
                                                              ↓
                                                 Grandma sees your face + subtitles
```

All audio processing is local. No cloud accounts. No API keys. No data leaves your machine.

---

## Downloads

> **Pre-built releases are coming soon** — subscribe to [Releases](https://github.com/BensonRen/Laolao/releases) on GitHub to be notified.

Until then, build from source in under 5 minutes — see **Quick Start** below.

---

## Demo

> **Screenshot placeholder** — launch `Laolao.app`, speak, and drop a screenshot here.

---

## What's included

| | |
|---|---|
| **Electron app** | One double-click launches everything — the caption server, virtual camera, and overlay window. No terminal, no OBS running. |
| **Chinese-first** | Mandarin (普通话) and Cantonese (粤语) in Simplified Chinese via OpenCC. Also English, Japanese, Korean, and 100+ languages. |
| **Low latency** | Partial captions ~500 ms after you start speaking; final text ~1 s after silence. Apple Silicon target: < 1.5 s end-to-end. |
| **Fully offline** | [mlx-whisper](https://github.com/ml-explore/mlx-examples) (Apple Silicon Neural Engine) or [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CPU/CUDA). No internet after first model download. |
| **Virtual camera** | Your captions overlay appears as **"OBS Virtual Camera"** in Zoom, FaceTime, WeChat, and any app with a camera picker. |
| **Hot-swap language** | Change language from the overlay toolbar mid-call — no restart needed. |
| **Customizable overlay** | Font size, text colors, background opacity, aspect ratio (9:16 / 16:9 / 4:3 / 1:1 / full), draggable caption block — all live. Settings persist. |
| **Mic permission detection** | If no audio signal is detected for 15 seconds, a banner appears with a one-click button to open OS mic settings. Auto-dismisses when signal recovers. |
| **Live debug panel** | Audio level meter, VAD dot, latency stats, and diagnostic hints in the toolbar. |

---

## Platform support

| Platform | Status | Acceleration |
|---|---|---|
| macOS Apple Silicon (M1/M2/M3/M4) | ✅ Supported | MLX — Neural Engine |
| macOS Intel | ⚠️ Untested | CPU faster-whisper |
| Windows 10/11 (x86-64) | ✅ Beta | CPU faster-whisper (CUDA if available) |
| Windows 11 on ARM64 (Snapdragon X / X2 Elite) | ✅ Beta | ONNX Runtime — **Hexagon NPU** via QNN |
| Linux | ❌ Not yet | — |

**Windows notes:**
- Requires OBS Studio 28+ installed (provides the virtual camera driver)
- Virtual camera requires a GUI/interactive session — does not work over SSH
- Tested on Intel i7 with OBS 30

**Snapdragon / ARM64 notes:** different lane entirely — no `faster-whisper`, no
Electron shell, and OBS installs itself. See
[Quick Start — Windows on ARM64](#quick-start--windows-on-arm64-snapdragon) below.

---

## Requirements

### macOS
- macOS 13 Ventura or later (Apple Silicon recommended)
- Python 3.10+ (`brew install python@3.12`)
- [OBS Studio 28+](https://obsproject.com/) installed (does **not** need to be running)
- A camera and a microphone (grant both when macOS prompts on first launch)

### Windows
- Windows 10 or 11
- Python 3.10+ from [python.org](https://www.python.org/downloads/)
- [OBS Studio 28+](https://obsproject.com/) installed (does **not** need to be running)
- A microphone

### Windows on ARM64 (Snapdragon)
- Windows 11 on a Snapdragon X / X2 Elite PC
- Python 3.10+ **ARM64 build** from [python.org](https://www.python.org/downloads/windows/) — pick *"Windows installer (ARM64)"*. An x64 Python cannot reach the NPU.
- OBS — **do not install it**; the launcher downloads a portable ARM64 copy itself
- A webcam, and **a headset** (see the constraints table — there is no echo cancellation on this path)
- No administrator rights are needed at any point

> **OBS conflict note:** Laolao and OBS share the same virtual camera slot ("OBS Virtual Camera"). If OBS's virtual camera is active at the same time as Laolao, they conflict — only one can use the slot at a time. Quit Laolao to hand it back to OBS, or vice versa.

---

## Quick Start — Mac

### 1. Install OBS Studio

Download [OBS Studio](https://obsproject.com/) (28 or later) and install it. You don't need to configure or run OBS — installing it registers the virtual camera driver.

### 2. Install Python 3.12

```bash
brew install python@3.12
```

### 3. Clone and set up

```bash
git clone https://github.com/BensonRen/Laolao
cd Laolao
chmod +x setup.sh
./setup.sh          # creates venv, installs all deps
```

The first run downloads the Whisper model (~465 MB for `small`).

### 4. Build the Electron app

```bash
cd electron
npm install
npm run build -- --mac dir    # → ../dist/mac-arm64/Laolao.app
```

The build signs the app with the camera + microphone entitlements
(`build/entitlements.mac.plist`) — these are **required**: under macOS's
hardened runtime, an app without the camera entitlement can open the webcam
but receives zero frames (a silent black screen). If a build ever fails at
the `codesign` step (its per-file network timestamp can flake), re-sign the
produced bundle without a timestamp:

```bash
codesign --deep --force --options runtime \
  --entitlements build/entitlements.mac.plist \
  --sign <your-signing-identity> ../dist/mac-arm64/Laolao.app
```

### 5. Install and launch

```bash
ditto ../dist/mac-arm64/Laolao.app /Applications/Laolao.app
open /Applications/Laolao.app
```

On first launch macOS prompts for **camera and microphone access** — grant
both. No admin password is needed: the virtual camera driver comes from OBS
Studio itself (installed in step 1). If OBS is missing, Laolao shows a dialog
pointing at the download page and runs in captions-only mode.

### 6. Select the camera in your call app

In Zoom / FaceTime / WeChat / etc., open camera settings and choose **"OBS Virtual Camera"**.

---

## Quick Start — Windows

### 1. Install OBS Studio

Download [OBS Studio](https://obsproject.com/) (28+) and install it.

### 2. Install Python 3.12

Download from [python.org](https://www.python.org/downloads/). During install, check **"Add Python to PATH"**.

### 3. Clone and set up

```cmd
git clone https://github.com/BensonRen/Laolao
cd Laolao
setup.bat
```

### 4. Build the Electron app

```cmd
cd electron
npm install
npm run build:win
```

This produces two files in `..\dist\`:
- **`Laolao Setup 0.1.0.exe`** — NSIS installer with Start Menu shortcut and optional install directory
- **`Laolao 0.1.0.exe`** — standalone portable executable, no install needed

### 5. Launch

Run the installer, or just double-click the portable `.exe` directly.

On first launch Windows will ask for **microphone access** — click Allow. If the mic access prompt doesn't appear automatically, click the orange **"Open Mic Settings"** banner that appears in the app after ~15 seconds and enable the microphone in Windows Privacy Settings.

### 6. Select the camera in your call app

Open camera settings in your video call app and choose **"OBS Virtual Camera"**.

---

## Quick Start — Windows on ARM64 (Snapdragon)

Snapdragon PCs run a different lane. `faster-whisper` cannot install there at
all — its `ctranslate2` dependency publishes no win-arm64 build — so Whisper
runs on **ONNX Runtime against the Hexagon NPU** instead. In practice a caption
appears about **0.85 s after you start speaking**, and the words reach the far
end's screen about **0.65 s** after that. (Whisper itself takes only ~90 ms per
pass on the NPU; the rest is voice-activity detection and frame timing. The
90 ms figure is the interesting one for a benchmark, not the one you feel.)

`pyvirtualcam` has no ARM64 wheel either, so a portable copy of **OBS ARM64 does
the compositing and provides the camera**, and there is no Electron app in the
loop.

The whole thing is one double-click.

### 1. Install Python (the only manual step)

Download from [python.org](https://www.python.org/downloads/windows/) and pick
**"Windows installer (ARM64)"** — 3.11 or 3.12. Tick **"Add python.exe to
PATH"**. The ARM64 build matters: an x64 Python runs under emulation and cannot
reach the NPU.

### 2. Get Laolao

```cmd
git clone https://github.com/BensonRen/Laolao
```

### 3. Double-click `Laolao-arm64.bat`

That is the whole setup. On the first run it creates the Python environment,
installs `requirements-arm64.txt`, downloads the Whisper NPU model (~200 MB)
and a portable OBS ARM64 (~167 MB), and registers the virtual camera **for your
user only** — no installer, no admin password, nothing written to `Program
Files`. Later runs take about 6 seconds, and running it twice is harmless.

### 4. Select the camera in your call app

The launcher ends by telling you this, but: in WeChat / Zoom / Teams, open the
video settings and choose **"OBS Virtual Camera"**. If the call app was already
open, quit it completely and reopen it — call apps only enumerate cameras at
launch.

Double-click **`Laolao-stop.bat`** when you're done; that hands your normal
webcam back to other apps.

| Command | What it does |
|---|---|
| `Laolao-arm64.bat` | Start everything (installs whatever is missing) |
| `Laolao-stop.bat` | Stop everything |
| `Laolao-arm64.bat -Status` | Report what is running |
| `Laolao-arm64.bat -Arch arm64` | Re-register the camera for ARM64-native call apps |
| `Laolao-arm64.bat -Setup` | Force the first-run setup to run again |

### Known constraints on ARM64

| Constraint | Why | What it means for you |
|---|---|---|
| Models limited to `tiny` / `base` / `large-v3-turbo` | Only those have a precompiled Qualcomm NPU export. `small` and `medium` fall back to the CPU and run ~25× slower | Nothing to do. `config.json` still says `small` (it is shared with Mac/x86), and the ARM64 backend substitutes **`large-v3-turbo`**, saying so in the log. That is the most accurate model, and the NPU runs it in ~410 ms — well inside budget. Set `"model": "base"` only if you want the fastest possible partials and can accept noticeably worse Chinese |
| First launch takes a couple of minutes | The Qualcomm NPU context for `large-v3-turbo` is compiled for your device the first time it loads (~117 s; ~6 s every launch after) | One-time. Setup does it up front so the first call is not the one that waits |
| Silero VAD runs, but not via `pip install silero-vad` | That package declares a hard PyTorch dependency, and torch has no win-arm64 build. The weights are plain ONNX, so Laolao runs them directly on onnxruntime instead | Nothing to do — it is automatic. Laolao fetches `silero_vad.onnx` (2.3 MB) on first run and caches it. If that download fails it falls back to the energy VAD, which only measures loudness: steady room noise above about −40 dBFS then reads as talking, and Whisper invents captions from it ("Thank you very much."). Check the log line `VAD: Silero via onnxruntime` to confirm which one you got |
| Echo cancellation is on | The caption window captures the mic through Chromium (which applies AEC) and streams it to the engine; Python runs `--no-mic` | Speakers are fine — the other person's voice is cancelled before it reaches Whisper, so it is not captioned as if you had said it. A headset is still the best option in a loud room |
| Reduced toolbar | OBS does the compositing here, so there is no Electron control window | The caption window carries a **Lang** dropdown — switch 普通话 / 粤语 / English / 日本語 / 한국어 mid-call and it takes effect immediately — plus the audio level meter and the 🌐 UI-language selector. Colours, caption size and aspect ratio are **not** adjustable on this path: OBS renders the overlay in its own embedded browser, which cannot see this window's settings. Change those in `config.json` and the overlay URL parameters, or use the Electron app |
| **The camera works for x64 apps *or* ARM64 apps, not both** | Windows-on-ARM64 has a single 64-bit COM slot for the camera, and OBS ships no ARM64X filter | Default is x64, which is what WeChat, Zoom and Teams are. If the camera shows up in the picker but the picture is **black**, your app is ARM64-native — run `Laolao-arm64.bat -Arch arm64` |
| Don't run the Electron app at the same time | Only one program can hold the physical webcam; the other one gets a black feed | The launcher closes Laolao's Electron window for you before OBS takes the camera |
| First run needs internet | The NPU model is a one-time ~200 MB download | Setup says so before it starts. After that Laolao never touches the network |
| Keep the folder path short | A deep install path makes the NPU model extract incompletely, and the only symptom is captions becoming ~25× slower — nothing errors | Setup warns past 90 characters. `setx LAOLAO_MODEL_DIR C:\laolao-models` moves just the model cache |
| `run.bat` / `setup.bat` don't apply | They install `faster-whisper` and expect a `venv\` | Use `Laolao-arm64.bat`; it maintains `.venv-arm64` |

Full engineering detail — including the alternative Electron + emulated-x64
camera path, kept documented as a fallback — is in
[`docs/snapdragon/`](docs/snapdragon/).

---

## Development mode

Run without building the packaged app:

```bash
cd electron && npm start
```

That's the whole dev loop — `main.js` spawns `server.py` and `virtual_cam.py` itself (supervised, auto-restarting). Don't also run `./run.sh` in another terminal or the two servers will fight over port 8765.

For server-only work (no Electron, no virtual camera), run the server and open the overlay standalone in Chrome:

```bash
./run.sh                                     # captures the mic via sounddevice
open -a "Google Chrome" overlay/index.html
```

Command-line options:

```bash
./run.sh --model base --language yue   # Cantonese, smaller model
./run.sh --list-devices                 # list microphone indices
./run.sh --benchmark                    # measure transcription latency
```

---

## Overlay Toolbar

The toolbar has two separate language controls: the caption-language dropdown (what Whisper transcribes) and a **🌐 UI-language selector** for the app chrome itself (简体中文 / 繁體中文 / English / 日本語 / 한국어, auto-detected from your OS on first run). It starts in **simple mode** for new users — language, level meter, camera, and help. The **高级 / Advanced** toggle reveals everything else.

| Control | Mode | What it does |
|---|---|---|
| **Lang** dropdown | simple | Switch caption language live — 普通话 / 粤语 / English / 日本語 / 한국어 / Auto |
| **Level bar + VAD dot** | simple | Audio pipeline health — green = speaking, yellow = buffering, red = no signal |
| **📷 Camera** | simple | Re-open the camera picker to switch input |
| **❓ Help** | simple | Re-open the first-call guide (how to pick the camera in WeChat) |
| **9:16 / 16:9 / 4:3 / 1:1 / Full / Custom** | advanced | Letterbox the output frame to that aspect ratio (camera stays 1280×720) |
| **🪞 Mirror** | advanced | Flip the self-view only — the far end always sees un-mirrored video |
| **📱 Safe** | advanced | Show phone-viewer safe-zone guides (self-view only, never transmitted) |
| **📐 Width** | advanced | Constrain caption max width (phone/square presets) |
| **🎨 Color** | advanced | Pick colors for final text, partial text, background, and opacity |
| **📊 Stats** | advanced | Debug panel: WebSocket status, backend, latency, mic RMS, VAD state |
| **▴ Hide** | advanced | Auto-hide the toolbar (hover the top edge to peek) |

Caption position is draggable via the **⠿ grip** on the caption block. Click **↩ reset** to return to default. All settings persist in `localStorage` and sync live to the output frame — the self-view is an accurate preview of what the far end sees, at any window size.

### Mic permission banner

If no audio signal is detected for 15 seconds after the app connects (RMS near zero), a red banner appears:

```
🎙 No mic signal detected.  [Open Mic Settings]  [✕]
```

Clicking **Open Mic Settings** opens:
- **macOS:** System Settings → Privacy & Security → Microphone
- **Windows:** Settings → Privacy → Microphone

The banner auto-dismisses when the mic signal recovers.

---

## Configuration (`config.json`)

```json
{
  "model":             "small",
  "language":          "zh",
  "device":            "auto",
  "compute_type":      "int8",
  "ws_port":           8765,
  "mic_device":        null,
  "chunk_ms":          250,
  "rolling_window_s":  5.0,
  "silence_chunks":    3,
  "silence_rms":       0.008,
  "show_partial":      true,
  "partial_interval_s": 0.35
}
```

| Key | Default | Notes |
|---|---|---|
| `model` | `small` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `language` | `zh` | Language code, or `null` for auto-detect |
| `device` | `auto` | `auto` / `mlx` / `cuda` / `cpu` |
| `chunk_ms` | `250` | Audio chunk size — lower = lower latency, higher CPU |
| `rolling_window_s` | `5.0` | Max audio fed to Whisper per pass |
| `silence_chunks` | `3` | Silent chunks before finalizing an utterance |
| `partial_interval_s` | `0.35` | How often to emit partial text during speech |

---

## Model guide

| Model | Size | Apple Silicon | Accuracy |
|---|---|---|---|
| `tiny` | 75 MB | ~50 ms/pass | Fair |
| `base` | 145 MB | ~100 ms/pass | Good |
| `small` | 465 MB | ~250 ms/pass | **Very good — recommended** |
| `medium` | 1.5 GB | ~600 ms/pass | Excellent |
| `large-v3` | 3 GB | ~1.5 s/pass | Best |

---

## Architecture

```
Laolao.app  (Electron)
│
├── main.js  (supervises both Python children; restarts them on crash)
│   ├── spawns server.py --no-mic   ← Python WebSocket + Whisper
│   ├── spawns virtual_cam.py       ← pyvirtualcam → OBS driver
│   ├── control window              ← overlay/index.html (what YOU see)
│   └── output window (offscreen)   ← overlay/index.html?output=1 (what THEY see)
│
├── output window — the SOLE camera consumer. Opens the webcam once
│   (getUserMedia), composes camera + captions, chrome-free, un-mirrored,
│   exactly 1280×720. Kept transparent/offscreen but "visible" to Chromium
│   so the camera keeps delivering frames.
│   capturePage() @ 30 fps → JPEG → TCP :8766 → virtual_cam.py
│                                                    ↓
│                                    OBS Camera Extension (Mac)
│                                    OBS VirtualCam DirectShow (Windows)
│                                          (what Zoom sees)
│
├── control window — toolbar, panels, mic capture, and a live PREVIEW of
│   the output frames over IPC (not its own camera). Your self-view is
│   literally what the far end sees. NEVER captured, so your controls
│   can't leak into the call.
│
└── settings sync: both windows share localStorage — camera, colors,
    caption position/width, ratio all mirror live into the output frame
```

Only **one** window ever opens the webcam. Opening the same camera twice
starves some USB webcams into black frames, and macOS gives a physical
camera to one app at a time — so a single consumer keeps the feed reliable
and, if capture ever fails, the device is released for other apps.

**Audio (unified path):** the control window captures your mic with
`getUserMedia` and **echo cancellation on**, then streams raw PCM to
`server.py` over the WebSocket. Echo cancellation matters in a real call:
without it, the other person's voice coming out of your speakers gets
transcribed and shown as *your* captions. Python never opens the mic
under Electron (standalone `./run.sh` still uses sounddevice).

```
server.py
│
├── WebSocket binary frames    16 kHz int16 PCM from the control window
├── VAD (Silero / Energy)      speech / silence detection
├── Whisper backend            local transcription
│   ├── MLX                    Apple Silicon Neural Engine
│   └── faster-whisper         CUDA / CPU (CTranslate2)
└── websockets                 JSON → overlay (partial + final captions)
```

**WebSocket messages (server → overlay):**
```json
{ "type": "partial", "text": "你好，奶奶" }
{ "type": "final",   "text": "你好，奶奶，我今天很好！" }
{ "type": "clear_partial" }
{ "type": "clear" }
{ "type": "stats",   "backend": "MLX", "model": "small" }
{ "type": "level",   "rms": 0.012, "vad": true, "buffer_s": 1.4 }
```

**WebSocket messages (overlay → server):** JSON `set_language` plus binary PCM audio frames:
```json
{ "type": "set_language", "language": "yue" }
```

**Camera geometry is fixed at 1280×720.** The OBS Camera Extension
delivers blank buffers at any other size (verified empirically — portrait
cameras enumerate but show a dead feed). Choosing 9:16 / 4:3 / 1:1 in the
toolbar letterboxes that shape *inside* the 16:9 frame with black bars;
phone viewers in fill mode crop the bars away and get the full-height
portrait view.

---

## Troubleshooting

**"OBS Virtual Camera" not visible in Zoom / WeChat**
- Make sure Laolao.app is running (it registers the camera on launch)
- Ensure OBS Studio 28+ is installed (its Camera Extension / DirectShow driver is required)
- Fully quit and reopen the call app after launching Laolao — apps enumerate cameras at launch
- Per-app camera picker locations are documented in [docs/COMPAT.md](docs/COMPAT.md)

**"OBS Studio Not Found" dialog on launch**
- Laolao has no virtual camera driver of its own; install [OBS Studio 28+](https://obsproject.com/) and relaunch
- Choosing "Continue Without Camera" runs captions-only mode: the caption window works, but no virtual camera appears in call apps

**Camera shows black bars on the sides**
- That's the aspect-ratio letterbox: you picked 9:16 / 4:3 / 1:1 in the toolbar, and the camera itself is always 1280×720 (a hard OBS Camera Extension limit)
- Phone viewers in fill mode crop the bars automatically — the far end on a phone sees the full-height portrait view
- Pick "Full" in the toolbar to fill the whole 16:9 frame

**Camera "in use" but the app shows a black screen**
- Almost always a macOS permission/signing issue, not a broken camera. Under the hardened runtime the app **must** be signed with the camera entitlement (`build/entitlements.mac.plist`) — without it, macOS opens the device (the green in-use light comes on) but delivers zero frames. A stock `npm run build` includes it; if you re-signed the app yourself, keep the entitlements.
- Grant camera access when prompted (System Settings → Privacy & Security → Camera → enable Laolao). If permission got into a bad state after rebuilding, reset it: `tccutil reset Camera com.laolao.app`, then relaunch.
- Every step of the camera pipeline is logged to `~/laolao-camera-debug.log` (truncated each launch) — `startCamera`, whether frames arrive, `black=true/false`. Attach it when filing a camera bug.
- A wedged USB webcam (e.g. after force-quitting the app repeatedly) can deliver a "live" track with no frames — the app walks a resolution ladder, then reopens the camera picker asking you to replug the USB or pick another camera.

**Another app (Google Meet, FaceTime) can't use my camera**
- While Laolao is running and actively capturing, it holds the physical camera — pick **"OBS Virtual Camera"** in the other app to see your Laolao feed (with captions) instead of the raw camera
- If Laolao's own capture fails it now **releases** the device, so other apps can use the raw camera directly; fully quitting Laolao always frees it

**Laolao and OBS both need the virtual camera**
- Only one app can use "OBS Virtual Camera" at a time
- Quit Laolao to release the slot to OBS, or vice versa

**No captions appear**
- Open the Stats panel in the overlay — "Disconnected" means the server isn't running
- Check `ws_port` in `config.json` matches the `port` URL param (default 8765)

**Red "no signal" banner appears (mic not working)**
- Click **Open Mic Settings** in the banner, or go to:
  - **macOS:** System Settings → Privacy & Security → Microphone → enable Laolao
  - **Windows:** Settings → Privacy → Microphone → toggle on
- Check the correct mic is selected: `./run.sh --list-devices`, then set `mic_device` in config

**VAD not triggering (audio detected but no captions)**
- Speak louder, or lower `silence_rms` in config (e.g. `0.004`)

**Captions are slow**
- Confirm `device` is `auto` or `mlx` in config (not `cpu`)
- Try `--model base`

**Traditional Chinese output instead of Simplified (or vice versa)**
- With `language` set to `zh` or `yue`, OpenCC converts Traditional → Simplified by default
- Prefer Traditional (HK/TW readers)? Set `"t2s": false` in `config.json`

**Windows: virtual camera not working from SSH**
- The OBS virtual camera driver requires an interactive GUI session on Windows
- Launch the app from the desktop, not via SSH

**Snapdragon/ARM64: "OBS Virtual Camera" appears in the picker but the picture is black**
- Your call app is ARM64-native, and the camera is registered for x64 apps (the default, because WeChat/Zoom are x64). Windows-on-ARM64 has only one 64-bit slot for it — run `Laolao-arm64.bat -Arch arm64` and reopen the call app
- Visible-but-dead is the expected symptom of the wrong architecture, not a broken install

**Snapdragon/ARM64: nothing starts, or it says the setup failed**
- `Laolao-arm64.bat -Status` reports what is up. The engine log is `..\laolao-tools\run\server.log`
- "No native ARM64 Python found" means the Python you installed is the x64 build — reinstall using the **ARM64** installer from python.org
- `Laolao-arm64.bat -Setup` redoes the environment from scratch

**Snapdragon/ARM64: my captions include the other person's voice**
- There is no echo cancellation on this path (Python captures the mic directly, not Chromium). Use a headset

**Snapdragon/ARM64: captions are suddenly much slower than the ~90 ms they should be**
- Almost always the install path being too long: the Qualcomm NPU model extracts incompletely and the backend silently falls back to the CPU. Move Laolao nearer the root of the drive, or `setx LAOLAO_MODEL_DIR C:\laolao-models` and re-run `Laolao-arm64.bat -Setup`
- Also check the engine log for `Model 'small' has no Qualcomm NPU export` — only `tiny`, `base` and `large-v3-turbo` run on the NPU

---

## Roadmap

### Own virtual camera (no OBS dependency)

Currently Laolao uses the OBS Camera Extension as its virtual camera driver, which means OBS must be installed, the two apps share the same camera slot, **and the camera is locked to 1280×720** — we verified empirically (2026-07) that the extension delivers blank buffers at any other size, which is why portrait output is composed via in-frame letterboxing instead of a real 720×1280 camera. The clean fix is a dedicated `CMIOExtension` (Apple, macOS 13+) for Mac — **"Laolao Camera"** independent of OBS, with native portrait support — and a dedicated DirectShow filter for Windows. Both require additional platform signing/certification.

### Smarter transcription

- **Custom vocabulary** — inject family names and domain terms as Whisper initial prompts
- **Rolling-window consensus** — reduce mid-word text flicker on long utterances ([whisper_streaming](https://github.com/ufal/whisper_streaming) algorithm)
- **Confidence filtering** — suppress low-confidence segments

### Two-way captioning

Caption grandma's side too: tap the call's system audio output as a second source, run a parallel pipeline, display both speakers in distinct colors.

### One-click distribution

- Signed & notarized DMG (Mac) with embedded Python runtime — no Homebrew, no terminal
- Signed NSIS installer (Windows)
- Auto-update via GitHub Releases
- Settings panel UI instead of hand-editing `config.json`

---

## Contributing

PRs and issues welcome. The project is intentionally small — `server.py` is the entire backend.

### Running tests

```bash
python tests/generate_test_audio.py   # generate fixtures (one-time)
pytest tests/ -m "not slow"           # fast tests
pytest tests/ -m slow -v -s           # inference tests (needs model downloaded)
pytest tests/test_latency.py -v -s    # latency benchmarks

# Windows (headless, SSH-runnable)
venv\Scripts\python -m pytest tests/test_windows_headless.py -v
```

### Adding a new backend

1. Create `backends/my_backend.py` implementing `BaseBackend`:
   ```python
   from backends.base import BaseBackend
   import numpy as np

   class MyBackend(BaseBackend):
       @classmethod
       def is_available(cls) -> bool: ...
       def transcribe(self, audio: np.ndarray, language: str | None) -> str: ...
   ```
2. Register it in `backends/__init__.py` — priority order is MLX → CUDA → CPU → yours

### Adding a new VAD

Same pattern under `vad/` — implement `BaseVAD` with `is_speech(chunk: np.ndarray) -> bool`.

### Good first issues

- [ ] Signed DMG / NSIS installer for one-click install
- [ ] Two-speaker mode — caption both sides of a call
- [ ] Font size slider in the toolbar (currently URL-param only)
- [ ] Cantonese-specific initial prompt tuning
- [ ] Linux virtual camera support (v4l2loopback)

---

## License

MIT — free to use, modify, and distribute.

---

## Credits

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 Whisper (MIT)
- [mlx-whisper](https://github.com/ml-explore/mlx-examples) — Apple Silicon Neural Engine (MIT)
- [Silero-VAD](https://github.com/snakers4/silero-vad) — neural voice activity detection (MIT)
- [OpenCC](https://github.com/BYVoid/OpenCC) — Traditional↔Simplified Chinese conversion (Apache 2.0)
- [sounddevice](https://python-sounddevice.readthedocs.io/) — audio capture (MIT)
- [websockets](https://websockets.readthedocs.io/) — WebSocket server (BSD)
- [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) — virtual camera output (MIT)
- [ONNX Runtime](https://onnxruntime.ai/) + [Qualcomm AI Hub](https://aihub.qualcomm.com/) — Whisper on the Hexagon NPU (MIT / BSD-3)
- [Electron](https://www.electronjs.org/) — desktop app shell (MIT)
- [OBS Studio](https://obsproject.com/) — Camera Extension / DirectShow driver (GPL)

---

*老老 (Lǎolao) — maternal grandmother in Mandarin. Built so she can follow the conversation.*
