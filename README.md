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
| Linux | ❌ Not yet | — |

**Windows notes:**
- Requires OBS Studio 28+ installed (provides the virtual camera driver)
- Virtual camera requires a GUI/interactive session — does not work over SSH
- Tested on Intel i7 with OBS 30

---

## Requirements

### macOS
- macOS 13 Ventura or later (Apple Silicon recommended)
- Python 3.10+ (`brew install python@3.12`)
- [OBS Studio 28+](https://obsproject.com/) installed (does **not** need to be running)
- A microphone

### Windows
- Windows 10 or 11
- Python 3.10+ from [python.org](https://www.python.org/downloads/)
- [OBS Studio 28+](https://obsproject.com/) installed (does **not** need to be running)
- A microphone

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

### 5. Install and launch

```bash
ditto ../dist/mac-arm64/Laolao.app /Applications/Laolao.app
open /Applications/Laolao.app
```

On first launch macOS will prompt for **microphone access** — grant it. The app also prompts once for your **admin password** to install the virtual camera driver.

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
npm run build:win    # → ..\dist\win-unpacked\Laolao.exe
```

### 5. Launch

Double-click `dist\win-unpacked\Laolao.exe`.

On first launch Windows will ask for **microphone access** — click Allow. If the mic access prompt doesn't appear automatically, click the orange **"Open Mic Settings"** banner that appears in the app after ~15 seconds and enable the microphone in Windows Privacy Settings.

### 6. Select the camera in your call app

Open camera settings in your video call app and choose **"OBS Virtual Camera"**.

---

## Development mode

Run without building the packaged app:

```bash
# Terminal 1 — caption server
./run.sh

# Terminal 2 — Electron shell (no build needed)
cd electron && npm start
```

Or open the overlay standalone in Chrome:

```bash
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

| Control | What it does |
|---|---|
| **Lang** dropdown | Switch language live — 普通话 / 粤语 / English / 日本語 / 한국어 / Auto |
| **9:16 / 16:9 / 4:3 / 1:1 / Full** | Constrain viewport to that aspect ratio |
| **📷 Camera** | Re-open the camera picker to switch input |
| **Level bar + VAD dot** | Audio pipeline health — green = speaking, yellow = buffering, red = no signal |
| **Color** | Pick colors for final text, partial text, background, and opacity |
| **Stats** | Debug panel: WebSocket status, backend, latency, mic RMS, VAD state |

Caption position is draggable via the **⠿ grip** on the caption block. Click **↩ reset** to return to default. All settings persist in `localStorage`.

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
├── main.js
│   ├── spawns server.py          ← Python WebSocket + Whisper
│   ├── spawns virtual_cam.py     ← pyvirtualcam → OBS Camera Extension
│   └── loads overlay/index.html  ← camera feed + caption UI
│
├── capturePage() @ 30 fps  →  TCP socket  →  virtual_cam.py
│                                                    ↓
│                                          pyvirtualcam (arm64 / x86-64)
│                                                    ↓
│                                    OBS Camera Extension (Mac)
│                                    OBS VirtualCam DirectShow (Windows)
│                                          (what Zoom sees)
│
└── overlay/index.html
    ├── getUserMedia          live camera feed (real webcam)
    ├── WebSocket client      auto-reconnect to server.py
    ├── Toolbar               language, ratio, color, drag, debug
    └── Mic warn banner       15 s silence → opens OS mic settings
```

```
server.py
│
├── sounddevice         16 kHz mono float32 audio
├── VAD (Silero / Energy)   speech / silence detection
├── Whisper backend     local transcription
│   ├── MLX             Apple Silicon Neural Engine
│   └── faster-whisper  CUDA / CPU (CTranslate2)
└── websockets          JSON → overlay (partial + final captions)
```

**WebSocket messages (server → overlay):**
```json
{ "type": "partial", "text": "你好，奶奶" }
{ "type": "final",   "text": "你好，奶奶，我今天很好！" }
{ "type": "clear" }
{ "type": "stats",   "backend": "MLX", "model": "small" }
{ "type": "level",   "rms": 0.012, "vad": true, "buffer_s": 1.4 }
```

**WebSocket messages (overlay → server):**
```json
{ "type": "set_language", "language": "yue" }
```

---

## Troubleshooting

**"OBS Virtual Camera" not visible in Zoom**
- Make sure Laolao.app is running (it registers the camera on launch)
- Ensure OBS Studio 28+ is installed (its Camera Extension / DirectShow driver is required)
- Fully quit and reopen Zoom after launching Laolao

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

**Traditional Chinese output instead of Simplified**
- Set `language` to `zh` or `yue` — OpenCC `t2s` conversion is automatic for those codes

**Windows: virtual camera not working from SSH**
- The OBS virtual camera driver requires an interactive GUI session on Windows
- Launch the app from the desktop, not via SSH

---

## Roadmap

### Own virtual camera (no OBS dependency)

Currently Laolao uses the OBS Camera Extension as its virtual camera driver, which means OBS must be installed and the two apps share the same camera slot. The clean fix is a dedicated `CMIOExtension` (Apple, macOS 13+) for Mac — **"Laolao Camera"** independent of OBS — and a dedicated DirectShow filter for Windows. Both require additional platform signing/certification.

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
- [Electron](https://www.electronjs.org/) — desktop app shell (MIT)
- [OBS Studio](https://obsproject.com/) — Camera Extension / DirectShow driver (GPL)

---

*老老 (Lǎolao) — maternal grandmother in Mandarin. Built so she can follow the conversation.*
