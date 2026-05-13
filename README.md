# 老老 Laolao — Real-time Chinese Captions for Video Calls

**Open source, fully offline, OBS-based real-time speech captions — Chinese-first.**

Built for families calling elderly relatives with hearing difficulties. Works with any video call platform (WeChat, WhatsApp, FaceTime, Zoom, etc.) via the OBS Virtual Camera.

```
You speak  →  Whisper (local)  →  OBS Browser Source (big text)  →  OBS Virtual Camera
                                                                          ↓
                                                         Grandma sees your face + captions
```

All processing is local. No cloud accounts, no API keys, no data leaves your computer.

---

## Features

- **Chinese-first**: optimized for Mandarin (普通话). Also supports Cantonese, English, Japanese, Korean, 100+ languages.
- **Low latency**: partial captions appear ~500-800ms after you start speaking; final text locks in 1.2s after you stop.
- **Fully offline**: uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (local Whisper model), no internet required after first model download.
- **OBS integration**: browser source overlay over your webcam feed via OBS Virtual Camera.
- **GPU acceleration**: 5-10x faster with NVIDIA GPU (CUDA).
- **Configurable**: font size, colors, model size, language all adjustable.

---

## Requirements

- **Python** 3.9 or newer
- **OBS Studio** 28+ (for Virtual Camera support)
- A microphone
- (Optional) NVIDIA GPU for faster transcription

---

## Quick Start

### Step 1 — Install

```bash
git clone https://github.com/YOUR_USERNAME/laolao
cd laolao
chmod +x setup.sh run.sh
./setup.sh        # creates venv, installs dependencies, downloads nothing yet
```

Windows:
```
setup.bat
```

### Step 2 — Configure OBS

1. Open **OBS Studio**
2. In your scene, click **+** → **Browser Source**
3. Check **"Local file"** and browse to `overlay/index.html`  
   *(or set URL to `file:///path/to/laolao/overlay/index.html`)*
4. Width: **1920**, Height: **1080** (match your OBS canvas)
5. **Uncheck** "Shutdown source when not visible"
6. Click **OK**

Resize and position the browser source at the bottom of your scene, above your webcam layer.

### Step 3 — Set up OBS Virtual Camera

1. In OBS: **Tools → Virtual Camera → Start Virtual Camera**
2. In your video call app (WeChat, WhatsApp Web, Zoom, etc.): select **OBS Virtual Camera** as the camera source

### Step 4 — Start Laolao

```bash
./run.sh
```

The first run downloads the Whisper model (~150 MB for `base`). Subsequent runs are instant.

You'll see:
```
12:34:56  INFO  Loading Whisper model 'base' on cpu ...
12:34:58  INFO  Model loaded.
12:34:58  INFO  Microphone open. Listening...
12:34:58  INFO  WebSocket server on ws://localhost:8765
```

Start talking. Captions appear in OBS.

---

## Model Size Guide

| Model | Size | Speed (CPU) | Chinese Accuracy | Recommended For |
|---|---|---|---|---|
| `tiny` | 75 MB | ~100ms/chunk | Fair | Very slow CPUs, testing |
| `base` | 145 MB | ~200ms/chunk | Good | **Default. Most users.** |
| `small` | 465 MB | ~400ms/chunk | Very good | Better accent handling |
| `medium` | 1.5 GB | ~1.2s/chunk | Excellent | CUDA GPU recommended |
| `large-v3` | 3 GB | ~3s/chunk | Best | CUDA GPU required |

Change in `config.json`:
```json
{ "model": "small" }
```

Or on the command line:
```bash
./run.sh --model small
```

---

## Language Configuration

```json
{ "language": "zh" }
```

| Code | Language |
|---|---|
| `zh` | Mandarin Chinese 普通话 (default) |
| `yue` | Cantonese 粤语 |
| `en` | English |
| `ja` | Japanese |
| `ko` | Korean |
| `auto` | Auto-detect (slight extra latency) |

---

## Latency Tuning

The biggest latency lever is `chunk_ms` and `partial_interval_s` in `config.json`:

```json
{
  "chunk_ms": 300,
  "partial_interval_s": 0.5,
  "silence_chunks": 4
}
```

**For minimum latency** (faster but higher CPU):
```json
{
  "chunk_ms": 200,
  "partial_interval_s": 0.4,
  "silence_chunks": 3
}
```

**For stability** (less flickering):
```json
{
  "chunk_ms": 400,
  "partial_interval_s": 0.8,
  "silence_chunks": 5
}
```

**With CUDA GPU** — set `device: "cuda"` and `compute_type: "float16"` for ~5x speedup, enabling real-time `small` or `medium` models.

---

## Overlay Customization

The OBS browser source URL supports query parameters:

```
file:///path/to/overlay/index.html?fontsize=96&maxlines=2
```

| Parameter | Default | Description |
|---|---|---|
| `fontsize` | `72` | Caption font size in pixels |
| `maxlines` | `3` | Max simultaneous caption lines |
| `fadems` | `4000` | Milliseconds before a line fades out |
| `bg` | `rgba(0,0,0,0.72)` | Caption background color |
| `chromakey` | *(flag)* | Transparent background (use OBS Chroma Key filter) |
| `port` | `8765` | WebSocket port (must match `ws_port` in config.json) |

Example — large text, 2 lines, 5 second fade:
```
file:///path/to/overlay/index.html?fontsize=96&maxlines=2&fadems=5000
```

---

## Choosing a Microphone

```bash
./run.sh --list-devices
```

Output:
```
Available audio input devices:
  [0] Built-in Microphone
  [1] USB Headset Microphone
  [2] Blue Yeti
```

Set in `config.json`:
```json
{ "mic_device": 2 }
```

Or:
```bash
./run.sh --mic 2
```

---

## GPU Acceleration (NVIDIA)

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

Then in `config.json`:
```json
{
  "device": "cuda",
  "compute_type": "float16",
  "model": "small"
}
```

With a mid-range GPU (RTX 3060), `small` model runs at ~80ms/chunk — smooth enough for `partial_interval_s: 0.3`.

---

## OBS Scene Setup (Recommended)

```
Scene: "Video Call with Captions"
│
├── [Browser Source] Laolao Captions  ← captions overlay
│     file:///path/to/overlay/index.html
│     Width: 1920, Height: 1080
│
└── [Video Capture Device] Your Webcam
      (positioned below browser source)
```

The captions float over your face. Grandma sees both your face and the text.

---

## Architecture

```
server.py
  │
  ├── sounddevice  →  raw PCM audio (16kHz mono float32)
  │                    ↓ every chunk_ms milliseconds
  ├── EnergyVAD    →  speech / silence detection
  │                    ↓ accumulates speech audio
  ├── faster-whisper → local Whisper transcription
  │                    ↓ partial (every 0.5s) + final (on silence)
  └── websockets   →  JSON to OBS browser source

overlay/index.html
  │
  ├── WebSocket client (auto-reconnect)
  ├── Renders "partial" text in yellow (in-progress)
  └── Renders "final" text in white (locked in, fades after N seconds)
```

**Message format** (WebSocket JSON):
```json
{ "type": "partial", "text": "你好，奶奶" }
{ "type": "final",   "text": "你好，奶奶，我今天很好！" }
{ "type": "clear" }
```

You can send messages to the overlay from any other program using this same WebSocket API.

---

## Troubleshooting

**No captions appearing:**
1. Check the terminal for errors
2. In OBS, right-click the browser source → **Interact** — do you see the overlay page?
3. Make sure the browser source URL is correct (use absolute path)
4. Check WebSocket port: browser source URL should match `ws_port` in config.json

**Wrong microphone being used:**
```bash
./run.sh --list-devices
./run.sh --mic 2
```

**Captions are slow / laggy:**
- Use a smaller model: `--model tiny` or `--model base`
- Lower `chunk_ms` to `200`
- If you have a GPU, enable CUDA

**Captions are cutting off mid-sentence:**
- Increase `rolling_window_s` to `6.0` or `8.0`
- Increase `silence_chunks` to `5` or `6`

**Chinese characters showing as boxes:**
- OBS's browser source should have CJK font support by default
- If not: install a CJK font (Noto Sans CJK) on your system

**macOS: microphone permission denied:**
- System Preferences → Privacy & Security → Microphone → allow Terminal / OBS

**Windows: sounddevice error:**
```bash
pip install pipwin
pipwin install pyaudio
```

---

## Contributing

PRs welcome. Key areas for improvement:

- [ ] Silero-VAD integration for more accurate speech detection
- [ ] whisper_streaming's "local agreement" algorithm for lower latency
- [ ] Cantonese (yue) specific model tuning
- [ ] OBS plugin version (avoid needing separate Python process)
- [ ] GUI for settings (tkinter or web-based)
- [ ] Two-way mode: also caption what grandma says

---

## License

MIT — free to use, modify, and distribute.

---

## Credits

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2-based Whisper (MIT)
- [OpenAI Whisper](https://github.com/openai/whisper) — the underlying model (MIT)
- [sounddevice](https://python-sounddevice.readthedocs.io/) — audio capture (MIT)
- [websockets](https://websockets.readthedocs.io/) — WebSocket server (BSD)
- [OBS Studio](https://obsproject.com/) — video mixer + virtual camera (GPL)

---

*老老 (Laolao) means "grandma" (maternal grandmother) in Mandarin.*
