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

## Demo

> **Screenshot placeholder** — launch `Laolao.app`, speak, and drop a screenshot here.

---

## What's included

| | |
|---|---|
| **Electron app** | One double-click launches everything — the caption server, virtual camera, and overlay window. No terminal, no OBS. |
| **Chinese-first** | Mandarin (普通话) and Cantonese (粤语) output in Simplified Chinese via OpenCC. Also supports English, Japanese, Korean, and 100+ languages. |
| **Low latency** | Partial captions ~500 ms after you start speaking; final text ~1 s after silence. Apple Silicon target: < 1.5 s end-to-end. |
| **Fully offline** | [mlx-whisper](https://github.com/ml-explore/mlx-examples) (Apple Silicon Neural Engine) or [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CPU/CUDA). No internet after first model download. |
| **Virtual camera** | Your captions overlay appears as **"OBS Virtual Camera"** in Zoom, FaceTime, WeChat, and any app with a camera picker. |
| **Hot-swap language** | Change language from the overlay toolbar mid-call — no restart needed. |
| **Customizable overlay** | Font size, text colors, background opacity, aspect ratio (9:16 / 16:9 / 4:3 / 1:1 / full), draggable caption block — all live. Settings persist. |
| **Live debug panel** | Audio level meter, VAD dot, latency stats, and diagnostic hints in the toolbar. |

---

## Requirements

- **macOS** (Apple Silicon — M1/M2/M3/M4)
- **Python 3.10+** (Homebrew recommended: `brew install python@3.12`)
- **OBS Studio 28+** installed — Laolao uses its Camera Extension driver. OBS does not need to be *running*; it just needs to be installed once.
- A microphone

> **Note on OBS:** Laolao and OBS share the same virtual camera slot. If you have OBS's virtual camera enabled at the same time as Laolao, they will conflict — only one can use it at a time. Quitting Laolao releases the slot back to OBS.

**Platform support:**

| Platform | Status | Acceleration |
|---|---|---|
| macOS Apple Silicon | ✅ Supported | MLX — Neural Engine |
| macOS Intel | ⚠️ Untested | CPU faster-whisper |
| Windows / Linux | ❌ Not yet | See Roadmap |

---

## Quick Start — Mac App

### 1. Install OBS Studio

Download and install [OBS Studio](https://obsproject.com/) (version 28 or later). You don't need to configure or run OBS — installing it registers the virtual camera driver that Laolao uses.

### 2. Install Python 3.12

```bash
brew install python@3.12
```

### 3. Clone and set up

```bash
git clone https://github.com/BensonRen/Laolao
cd Laolao
chmod +x setup.sh
./setup.sh          # creates venv with Python 3.12, installs all deps
```

The first run downloads the Whisper model (~465 MB for `small`).

### 4. Build the Electron app

```bash
cd electron
npm install
npm run build -- --mac dir    # builds to ../dist/mac-arm64/Laolao.app
```

### 5. Install

```bash
ditto ../dist/mac-arm64/Laolao.app /Applications/Laolao.app
```

Then launch `/Applications/Laolao.app`.

On first launch:
- macOS will ask for your **microphone permission** — grant it
- macOS will ask for your **admin password** once to install the virtual camera driver

### 6. Select the camera in your call app

In Zoom / FaceTime / WeChat / etc., open camera settings and select **"OBS Virtual Camera"**. Grandma now sees your face with live captions.

---

## Development mode

To run without building the app:

```bash
# Terminal 1 — caption server
./run.sh

# Terminal 2 — open overlay directly
open -a "Google Chrome" overlay/index.html
```

Or run the Electron shell in dev mode (no build needed):

```bash
cd electron && npm start
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
| **Lang** dropdown | Switch transcription language live (普通话 / 粤语 / English / 日本語 / 한국어 / Auto) |
| **9:16 / 16:9 / 4:3 / 1:1 / Full** | Constrain the viewport to that aspect ratio |
| **📷 Camera** | Re-open the camera picker to switch input |
| **Level bar + VAD dot** | Audio pipeline health — green = speaking, yellow = buffering, red = no signal |
| **Color** | Pick colors for final text, partial text, background, and opacity |
| **Stats** | Debug panel: WebSocket status, backend, latency, mic RMS, VAD state |

Caption position is draggable via the **⠿ grip** on the caption block. Click **↩ reset** to return to default. All settings persist in `localStorage`.

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

## Model Guide

| Model | Download | Apple Silicon | Accuracy |
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
│                                          pyvirtualcam (arm64)
│                                                    ↓
│                                       OBS Camera Extension
│                                          (what Zoom sees)
│
└── overlay/index.html
    ├── getUserMedia          live camera feed (real webcam)
    ├── WebSocket client      auto-reconnect to server.py
    └── Toolbar               language, ratio, color, drag, debug
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
- Ensure OBS Studio 28+ is installed (its Camera Extension is the driver)
- Fully quit and reopen Zoom after launching Laolao

**Laolao and OBS both need the virtual camera at the same time**
- Only one app can use "OBS Virtual Camera" at a time — it's a single-producer device
- Quit Laolao to release the slot back to OBS, or vice versa

**No captions / nothing appears**
- Open Stats panel in the overlay — "Disconnected" means the server isn't running
- Check that `ws_port` in `config.json` matches the `port` URL param (default 8765)

**Audio level bar stays red ("no signal")**
- System Settings → Privacy & Security → Microphone → allow Laolao
- Check correct mic: `./run.sh --list-devices` then set `mic_device` in config

**VAD not triggering**
- Speak louder, or lower `silence_rms` in config (e.g. `0.004`)

**Captions are slow**
- Confirm `device` is `auto` or `mlx` in config (not `cpu`)
- Try `--model base`

**Traditional Chinese output instead of Simplified**
- Set `language` to `zh` or `yue` — OpenCC `t2s` conversion is automatic for those codes

---

## Roadmap

### Own virtual camera (no OBS dependency)

Currently Laolao uses the OBS Camera Extension as its virtual camera driver, which means OBS must be installed and the two apps share the same camera slot. The clean fix is to build Laolao its own `CMIOExtension` (Apple's Camera Extension API, macOS 13+) — a separate device that shows up as **"Laolao Camera"** independently of OBS. Requires an Apple Developer account and a Camera Extension entitlement.

### Windows support

- [whisper.cpp](https://github.com/ggerganov/whisper.cpp) backend for Windows ARM (Snapdragon X)
- DirectShow virtual camera for Windows (replaces the OBS Camera Extension approach)

### Smarter transcription

- **Custom vocabulary** — inject family names and domain terms as Whisper initial prompts
- **Rolling-window consensus** — reduce mid-word text flicker on long utterances ([whisper_streaming](https://github.com/ufal/whisper_streaming) algorithm)
- **Confidence filtering** — suppress low-confidence segments

### Two-way captioning

Caption grandma's side too: tap the call's system audio output as a second source, run a parallel pipeline, display both speakers in distinct colors.

### Distribution

- Signed & notarized DMG with embedded Python runtime — no Homebrew, no terminal
- Auto-update via GitHub Releases
- Settings panel UI instead of hand-editing `config.json`

---

## Contributing

PRs and issues welcome. The project is intentionally small — `server.py` is the entire backend.

### Running tests

```bash
python tests/generate_test_audio.py   # generate fixtures (one-time)
pytest tests/ -m "not slow"           # fast tests
pytest tests/ -m slow -v -s           # inference tests (needs model)
pytest tests/test_latency.py -v -s    # latency benchmarks
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

- [ ] Windows testing and CI
- [ ] Two-speaker mode — caption both sides of a call
- [ ] Font size slider in the toolbar (currently URL-param only)
- [ ] Cantonese-specific initial prompt tuning
- [ ] Signed DMG for one-click install

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
- [OBS Studio](https://obsproject.com/) — Camera Extension driver (GPL)

---

*老老 (Lǎolao) — maternal grandmother in Mandarin. Built so she can follow the conversation.*
