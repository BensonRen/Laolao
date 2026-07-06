# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Laolao (老老) is a fully offline real-time speech-to-text captioning tool for video calls, Chinese-first (Mandarin/Cantonese, Traditional→Simplified via OpenCC). An Electron app composites live captions over the user's webcam feed and publishes the result as a virtual camera ("OBS Virtual Camera") that Zoom/WeChat/FaceTime select as their camera. All Whisper inference is local — no cloud APIs.

## Commands

```bash
# Setup (creates venv, installs deps, auto-detects macOS/CUDA)
./setup.sh                          # macOS/Linux; setup.bat on Windows

# Python server standalone
./run.sh                            # run.bat on Windows
./run.sh --model small --language yue
./run.sh --list-devices             # List microphone indices
./run.sh --benchmark                # Measure transcription latency
./run.sh --no-mic                   # Skip sounddevice; audio arrives over WebSocket

# Electron dev mode (no build): server in one terminal, shell in another
./run.sh                            # Terminal 1
cd electron && npm start            # Terminal 2

# Package the app
cd electron && npm run build        # macOS DMG → dist/
cd electron && npm run build:win    # Windows NSIS + portable

# Tests
pytest tests/                               # All tests
pytest tests/ -m "not slow"                 # Skip Whisper inference tests (use in CI)
pytest tests/ -m slow -v -s                 # Inference tests (needs tiny model downloaded)
pytest tests/test_latency.py -v -s          # Latency benchmarks
pytest tests/test_virtualcam_macos.py -v -s # macOS virtual camera e2e diagnostic
pytest tests/test_windows_headless.py -v -s # Windows headless e2e (SSH-runnable)

# Generate test fixtures before running audio-dependent tests
python tests/generate_test_audio.py
python tests/download_audio.py --url "<youtube_url>" --duration 60
```

## Architecture

Three processes, orchestrated by Electron (`electron/main.js`):

```
Electron main.js
├── spawns server.py (venv python)      Whisper + VAD + WebSocket :8765
├── spawns virtual_cam.py               pyvirtualcam frame sink, TCP :8766
├── BrowserWindow loads overlay/index.html   (camera feed + captions UI)
└── capturePage() @ 30fps → JPEG → TCP :8766 → virtual_cam.py → OS virtual camera
```

**Audio paths — platform-dependent:**
- macOS: `server.py` captures the mic directly via sounddevice (16kHz mono) on a thread that feeds `UtteranceProcessor`.
- Windows: Python never touches the mic (Windows Store Python sandboxing). Electron spawns `server.py --no-mic`; the overlay captures mic audio via getUserMedia and streams raw int16 PCM as **binary WebSocket frames** to the server (`feed_electron_audio`).

**server.py** — the captioning engine. Audio chunks flow through `UtteranceProcessor`: VAD gates accumulation into a rolling buffer; partials are transcribed every `partial_interval_s` while speaking; `silence_chunks` consecutive silent chunks finalize the utterance. Transcription runs on a dedicated worker thread (`_tx_worker`) so slow Whisper passes never block the audio thread — pending partials are coalesced (only newest kept), finals always queue. Hallucinations are rejected by a chars-per-second plausibility cap (10 for CJK, 20 otherwise). Language can be hot-swapped mid-call from the overlay via a `set_language` WS message.

**WebSocket protocol** (port 8765):
- Server → overlay (JSON): `{"type": "partial"|"final", "text": ...}`, `{"type": "stats", ...}` (backend/latency debug), `{"type": "level", ...}` (audio meter/VAD state, ~8Hz)
- Overlay → server: JSON `{"type": "set_language", "language": "yue"}`; **binary frames are int16 PCM audio** (Windows path)

**Pluggable backends** (`backends/`): auto-selects MLX (Apple Silicon) → CUDA faster-whisper → CPU faster-whisper. Each implements `transcribe(audio, language) -> str` and `is_available() -> bool`.

**Pluggable VAD** (`vad/`): auto-selects Silero-VAD (ONNX) → EnergyVAD (RMS fallback).

**Overlay** (`overlay/index.html`): single self-contained HTML file — camera feed via getUserMedia (never OBS compositing), caption rendering, toolbar (language, aspect ratio, colors, mirror, safe zones, drag), mic-permission warning banner, and the Windows mic→WebSocket streamer. Also usable standalone in Chrome or as an OBS browser source. URL params: `fontsize`, `maxlines`, `fadems`, `bg`, `port`.

**Virtual camera** (`virtual_cam.py`): receives `[4-byte BE length][JPEG]` frames over TCP and pushes them via pyvirtualcam. On macOS 14+ this targets the **OBS Camera Extension** — the DAL plugin in `electron/resources/` (installed by main.js to `/Library/CoreMediaIO`) is the legacy path; `vcam/` contains an experimental in-house Mach-server dylib. OBS Studio 28+ must be installed on both platforms for the driver.

## Platform gotchas

- **Packaged app depends on the repo checkout**: `electron/main.js` hardcodes `ROOT = ~/code/Laolao` when packaged and runs `venv/bin/python` + scripts from there. The DMG is a shell; it does not bundle Python.
- Packaged-app resources (e.g. the DAL plugin) resolve via `process.resourcesPath`, not `__dirname` (which points inside app.asar).
- On Windows, main.js prepends `C:\Program Files\obs-studio\bin\64bit` to PATH so pyvirtualcam can load the OBS DirectShow DLL.
- The BrowserWindow uses `paintWhenInitiallyHidden` + disabled background throttling — without these, `capturePage()` returns black frames when the window is occluded (e.g. WeChat in front) and the virtual camera goes dark.
- Temp/log paths must use `tempfile.gettempdir()`, not `/tmp` (doesn't exist on Windows).
- The virtual camera requires a GUI session — it does not work over SSH.

## Key configuration (`config.json`)

| Key | Default | Effect |
|-----|---------|--------|
| `model` | `base` | tiny/base/small/medium/large-v3 |
| `language` | `zh` | zh/yue/en/ja/ko/auto |
| `device` | `auto` | auto/cpu/cuda/mlx |
| `vad` | `auto` | auto/silero/energy |
| `chunk_ms` | `300` | Audio chunk size — lower = lower latency, higher CPU |
| `rolling_window_s` | `4.0` | Max audio fed to Whisper per pass |
| `silence_chunks` | `4` | Silent chunks before finalizing utterance |
| `partial_interval_s` | `0.5` | Frequency of partial text updates during speech |
| `ws_port` | `8765` | WebSocket port (CLI flag is `--port`, not `--ws-port`) |
| `mic_device` | `null` | Mic index/name substring; null = system default |

## Testing notes

- `tests/fixtures/` is gitignored — must be generated locally before inference tests run
- Markers: `@pytest.mark.slow` (requires model download), `@pytest.mark.macos_only`
- `conftest.py` provides shared pytest fixtures for backend, VAD, and audio samples
- `test_virtualcam_macos.py` / `test_windows_headless.py` / `test_mic_e2e_windows.py` are e2e diagnostics that exercise the real camera/mic pipeline, not just units
- Latency target: ~500–800ms partial, ~1.2s final on Apple Silicon with `base` model
