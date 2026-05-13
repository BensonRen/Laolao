# 老老 Laolao — Real-time Speech Captions for Video Calls

**Fully offline, open source, Chinese-first live captioning — runs on your Mac or PC, no cloud required.**

Built for families calling elderly relatives with hearing difficulties. Speak naturally; giant subtitles appear on your camera feed in real time via OBS Virtual Camera. Works with WeChat, WhatsApp, FaceTime, Zoom, and any other video call platform.

```
You speak  →  local Whisper  →  overlay/index.html  →  OBS Virtual Camera
                                      (captions)               ↓
                                                  Grandma sees your face + subtitles
```

All audio processing is local. No cloud accounts. No API keys. No data leaves your machine.

---

## Demo

> **Screenshot placeholder** — run `./run.sh`, open `overlay/index.html` in Chrome, and speak. The overlay shows live captions over your camera feed. Drop a screenshot here once you have one.

<table>
<tr>
<td align="center"><b>Portrait mode (9:16)</b><br><i>ideal for phone-style calls</i></td>
<td align="center"><b>Widescreen (16:9)</b><br><i>standard webcam layout</i></td>
</tr>
<tr>
<td align="center">[ screenshot ]</td>
<td align="center">[ screenshot ]</td>
</tr>
</table>

---

## Features

| | |
|---|---|
| **Chinese-first** | Mandarin (普通话) and Cantonese (粤语) output in Simplified Chinese via OpenCC. Also supports English, Japanese, Korean, and 100+ languages. |
| **Low latency** | Partial captions appear ~500 ms after you start speaking; final text locks in ~1 s after silence. Apple Silicon target: < 1.5 s end-to-end. |
| **Fully offline** | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) or [mlx-whisper](https://github.com/ml-explore/mlx-examples) for Apple Silicon Neural Engine. No internet after first model download. |
| **Hot-swap language** | Change language from the overlay toolbar mid-call — no server restart needed. |
| **Pluggable backends** | Auto-selects: MLX (Apple Silicon) → CUDA faster-whisper → CPU faster-whisper. |
| **Pluggable VAD** | Auto-selects: Silero-VAD (ONNX neural) → Energy VAD (RMS fallback). |
| **Live debug panel** | Audio level meter, VAD dot, transcription stats, and diagnostic hints — all in the overlay toolbar. |
| **Customizable overlay** | Font size, text colors, background opacity, aspect ratio (9:16 / 16:9 / 4:3 / 1:1 / full), draggable caption position — all live via the toolbar. Settings persist across reloads. |
| **OBS chroma key** | Add `?chromakey` to the URL for a transparent background you can key out in OBS. |

---

## Requirements

- **Python** 3.9 or newer
- **OBS Studio** 28+ with Virtual Camera
- A microphone

**Platform support:**

| Platform | Acceleration |
|---|---|
| macOS (Apple Silicon) | MLX — Neural Engine (~200–300 ms/pass on `small`) |
| macOS (Intel) | CPU faster-whisper |
| Linux / Windows | CUDA faster-whisper (NVIDIA GPU) or CPU |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/BensonRen/Laolao
cd Laolao
chmod +x setup.sh run.sh
./setup.sh          # creates venv, installs deps — auto-detects macOS vs CUDA
```

Windows:
```
setup.bat
```

> **NVIDIA GPU?** Install PyTorch with CUDA first, then run setup:
> ```bash
> pip install torch --index-url https://download.pytorch.org/whl/cu121
> ./setup.sh
> ```
> Then set `"device": "cuda"` and `"compute_type": "float16"` in `config.json`.

### 2. Start the caption server

```bash
./run.sh
```

The first run downloads the Whisper model (~465 MB for `small`). After that it starts instantly:

```
12:34:56  INFO  Backend: MLX  model=small
12:34:58  INFO  VAD: SileroVAD
12:34:58  INFO  Microphone open — listening on device 0
12:34:58  INFO  WebSocket server on ws://localhost:8765
```

### 3. Open the overlay

Open `overlay/index.html` in **Chrome** (file → open, or drag the file into Chrome). Grant camera and microphone permissions when prompted.

You'll see:
- Your camera feed filling the page
- A toolbar at the top for language, aspect ratio, colors, and debug stats
- Captions appear at the bottom as you speak

### 4. Set up OBS

1. **OBS → Scene → Add → Browser Source**
2. Check **Local file** and browse to `overlay/index.html`
3. Width: **1080**, Height: **1920** for portrait (9:16), or **1920 × 1080** for widescreen
4. Uncheck "Shutdown source when not visible"
5. **Tools → Virtual Camera → Start**
6. In your video call app: select **OBS Virtual Camera** as the camera

Grandma now sees your face with captions overlaid.

---

## Overlay Toolbar

The overlay has a built-in control bar — no need to edit config files for common adjustments:

| Control | What it does |
|---|---|
| **Lang** dropdown | Switch transcription language live (普通话 / 粤语 / English / 日本語 / 한국어 / Auto) |
| **9:16 / 16:9 / 4:3 / 1:1 / Full** | Constrain the viewport to that aspect ratio (CSS-based, always works) |
| **Level bar + VAD dot** | Always-on audio pipeline health — green = speaking, yellow = buffering, red = no signal |
| **Color** | Pick colors for final text, partial text, background, and background opacity |
| **Stats** | Debug panel: WebSocket status, backend, latency stats, mic RMS, VAD state, diagnostic hints |

Caption position is draggable via the **⠿ grip** on the left of the subtitle block. Click **↩ reset** to return to the default position. All settings persist in `localStorage`.

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

Command-line overrides (any config key):
```bash
./run.sh --model base --language yue
./run.sh --list-devices     # print microphone indices
./run.sh --benchmark        # measure transcription latency
```

---

## Model Guide

| Model | Download | CPU speed | Apple Silicon | Accuracy |
|---|---|---|---|---|
| `tiny` | 75 MB | ~100 ms/pass | ~50 ms | Fair |
| `base` | 145 MB | ~200 ms/pass | ~100 ms | Good |
| `small` | 465 MB | ~800 ms/pass | ~250 ms | **Very good — recommended** |
| `medium` | 1.5 GB | ~2 s/pass | ~600 ms | Excellent |
| `large-v3` | 3 GB | ~5 s/pass | ~1.5 s | Best |

On Apple Silicon, `small` via MLX hits < 1.5 s end-to-end latency and is the recommended default.

---

## Overlay URL Parameters

For use as a standalone OBS browser source (without the toolbar camera view):

```
file:///path/to/overlay/index.html?fontsize=96&maxlines=2&fadems=5000
```

| Parameter | Default | Description |
|---|---|---|
| `fontsize` | `52` | Caption font size in px |
| `maxlines` | `3` | Max simultaneous caption lines |
| `fadems` | `4000` | Milliseconds before a line fades |
| `bg` | `rgba(0,0,0,0.72)` | Caption background (overridden by Color picker) |
| `port` | `8765` | WebSocket port — must match `ws_port` in `config.json` |
| `chromakey` | *(flag)* | Green background for OBS Chroma Key filter |

---

## Architecture

```
server.py
│
├── sounddevice         16 kHz mono float32 audio
│        ↓ every chunk_ms
├── VAD (Silero / Energy)   speech / silence detection
│        ↓ accumulates speech audio
├── Whisper backend     local transcription
│   ├── MLX             Apple Silicon Neural Engine
│   └── faster-whisper  CUDA / CPU (CTranslate2)
│        ↓ partial (every partial_interval_s) + final (on silence)
└── websockets          JSON broadcast → overlay
```

```
overlay/index.html
│
├── getUserMedia        live camera feed
├── WebSocket client    auto-reconnect, bidirectional
│   ├── ← partial / final / clear / stats / level  (from server)
│   └── → set_language                             (to server)
└── Toolbar             language, ratio, color, drag, debug
```

**WebSocket messages (server → overlay):**
```json
{ "type": "partial", "text": "你好，奶奶" }
{ "type": "final",   "text": "你好，奶奶，我今天很好！" }
{ "type": "clear" }
{ "type": "stats",   "backend": "MLX", "model": "small", ... }
{ "type": "level",   "rms": 0.012, "vad": true, "buffer_s": 1.4, ... }
```

**WebSocket messages (overlay → server):**
```json
{ "type": "set_language", "language": "yue" }
```

Any program can connect to `ws://localhost:8765` and receive or send these messages.

---

## Troubleshooting

**No captions / nothing appears**
- Check server terminal for errors
- Open the overlay in Chrome and check the Stats panel — "Disconnected" means the server isn't running
- Verify `ws_port` in `config.json` matches the `port` URL param (default 8765)

**Audio level bar stays red ("no signal")**
- macOS: System Settings → Privacy & Security → Microphone → allow Terminal
- Check the correct microphone: `./run.sh --list-devices` then set `mic_device` in config

**VAD not triggering (bar shows signal but dot stays grey)**
- You may need to speak louder — lower `silence_rms` in config (e.g. `0.004`)
- The Stats panel shows diagnostic hints when this is detected

**Captions are slow**
- Apple Silicon: make sure `device` is `auto` or `mlx` (not `cpu`)
- Try a smaller model: `--model base`
- Lower `chunk_ms` to `200` and `partial_interval_s` to `0.3`

**Traditional Chinese output instead of Simplified**
- Ensure `language` is `zh` or `yue` — OpenCC `t2s` conversion is automatic for those codes

**Subtitle area disappeared**
- The drag position may have been saved off-screen — reload the page; it auto-corrects on load

---

## Roadmap

What we'd love to build next, roughly in priority order.

### Drop the OBS dependency

OBS is the biggest setup hurdle for non-technical users. The overlay already composites camera + captions in a single browser window — the only thing OBS provides is a *virtual camera driver* that video call apps can select. Three paths forward:

- **Electron app** *(recommended)* — wraps `overlay/index.html` in an Electron shell, bundles the Python server as a sidecar, and exposes a virtual camera via a native module. Reduces setup to a single double-click. The overlay UI needs no changes.
- **macOS native helper** — use `ScreenCaptureKit` to capture the Chrome window and push frames into a `CoreMediaIO` virtual camera extension (Apple's official API, macOS 13+). Small Swift binary, no Electron.
- **ffmpeg loopback** *(quick hack)* — `ffmpeg` window capture piped to `v4l2loopback` (Linux) or the standalone OBS-VirtualCam driver (Windows/Mac). No new code, but fragile.

### Smarter transcription

- **Custom vocabulary** — let users specify family names, place names, or domain terms that Whisper consistently mishears; inject them as initial prompt hints
- **Local-agreement algorithm** — implement `whisper_streaming`'s rolling-window consensus to reduce mid-word text flicker on long utterances
- **Confidence filtering** — suppress low-confidence segments rather than showing garbled output

### Two-way captioning

Caption grandma's side of the call too. Requires tapping the call's system audio output (loopback) as a second audio source, running a parallel transcription pipeline, and displaying both speakers' text in distinct colors.

### Grandma-side companion

A simple web page (or phone app) grandma opens on her own device. Captions are relayed over the internet via a lightweight signaling server — the text appears large on her screen independently of the call video, useful when video quality is poor.

### Distribution

- **One-click installer** — bundled Electron or native app with embedded Python runtime; no terminal required
- **Font size slider** in the toolbar (currently URL-param only)
- **Settings panel** — persistent config UI instead of hand-editing `config.json`
- **Auto-update** — check GitHub releases and notify in the toolbar

---

## Contributing

PRs and issues welcome. The project is intentionally small and readable — `server.py` is the entire backend.

### Running tests

```bash
# Generate fixtures first (one-time)
python tests/generate_test_audio.py

# All tests (skips slow inference tests)
pytest tests/ -m "not slow"

# Full suite including Whisper inference (requires model downloaded)
pytest tests/ -m slow -v -s

# Latency benchmarks
pytest tests/test_latency.py -v -s
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
- [ ] Electron wrapper so OBS isn't required

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
- [OBS Studio](https://obsproject.com/) — video mixer and virtual camera (GPL)  

---

*老老 (Lǎolao) — maternal grandmother in Mandarin. Built so she can follow the conversation.*
