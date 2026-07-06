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

# Electron dev mode (no build) — main.js spawns server.py and virtual_cam.py
# itself (supervised); do NOT also run ./run.sh or the two servers fight over :8765
cd electron && npm start

# Package the app
cd electron && npm run build        # macOS DMG → dist/
cd electron && npm run build:win    # Windows NSIS + portable

# Tests
pytest tests/                               # All tests
pytest tests/ -m "not slow"                 # Skip Whisper inference tests (use in CI)
pytest tests/ -m slow -v -s                 # Inference tests (needs tiny model downloaded)
pytest tests/test_latency.py -v -s          # Latency benchmarks
pytest tests/test_virtualcam_macos.py -v -s # macOS virtual camera e2e diagnostic
pytest tests/test_callapp_compat_macos.py -v -s  # call-app compatibility (AVFoundation enumeration)
pytest tests/test_windows_headless.py -v -s # Windows headless e2e (SSH-runnable)

# Generate test fixtures before running audio-dependent tests
python tests/generate_test_audio.py
python tests/download_audio.py --url "<youtube_url>" --duration 60
```

## Architecture

Three processes and **two windows**, orchestrated by Electron (`electron/main.js`):

```
Electron main.js
├── spawns server.py --no-mic (venv python)   Whisper + VAD + WebSocket :8765
├── spawns virtual_cam.py 1280 720 30         pyvirtualcam frame sink, TCP :8766
├── control window   overlay/index.html            visible: toolbar, panels,
│                                                  mirrored self-view, mic capture
└── output window    overlay/index.html?output=1   hidden, exactly 1280×720,
                                                   chrome-free, un-mirrored
        └── capturePage() @ 30fps → JPEG(92) → TCP :8766 → virtual_cam.py → OS camera
```

Only the **output window** is captured into the virtual camera — the control UI can never leak to the far end. Both windows share settings via localStorage (`storage` events); the output window is read-only on localStorage and never streams the mic. Children are **supervised** (restart with exponential backoff on crash, `isQuitting` gate) and startup uses TCP port readiness polling, not sleeps (first run downloads models — the server gets a 120s budget).

**Audio path — unified across platforms:** under Electron, the control window captures the mic via getUserMedia with **`echoCancellation: true`** (so the far end's voice coming out of the speakers is not captioned back) and streams raw int16 PCM as **binary WebSocket frames**; `server.py` always runs `--no-mic`. Standalone `./run.sh` (no Electron) still captures via sounddevice. Either way, all audio lands in `_audio_q` and a single consumer thread calls `UtteranceProcessor.feed()` — nothing heavy ever runs on the asyncio event loop.

**server.py** — the captioning engine. Audio chunks flow through `UtteranceProcessor`: VAD gates accumulation into a rolling buffer; partials are transcribed every `partial_interval_s` while speaking; `silence_chunks` consecutive silent chunks finalize the utterance. If an utterance outgrows `rolling_window_s`, the whole buffer is **segment-committed** as a final caption (long sentences never lose their start) with a 0.5s overlap tail. Transcription runs on a dedicated worker thread (`_tx_worker`) so slow Whisper passes never block audio — pending partials are coalesced (only newest kept), finals always queue. Hallucinations are rejected by a chars-per-second plausibility cap (10 for CJK, 20 otherwise); rejected/empty finals emit `clear_partial` so stale partial text doesn't linger. Language can be hot-swapped mid-call via the `set_language` WS message.

**WebSocket protocol** (port 8765):
- Server → overlay (JSON): `{"type": "partial"|"final", "text": ...}`, `{"type": "clear_partial"}` (drop the pending partial), `{"type": "clear"}`, `{"type": "stats", ...}` (backend/latency debug), `{"type": "level", ...}` (audio meter/VAD state, ~8Hz)
- Overlay → server: JSON `{"type": "set_language", "language": "yue"}`; **binary frames are int16 PCM mic audio** (Electron control window, all platforms)

**Pluggable backends** (`backends/`): auto-selects MLX (Apple Silicon) → CUDA faster-whisper → CPU faster-whisper. Each implements `transcribe(audio, language) -> str` and `is_available() -> bool`.

**Pluggable VAD** (`vad/`): auto-selects Silero-VAD (ONNX) → EnergyVAD (RMS fallback).

**Overlay** (`overlay/index.html`): single self-contained HTML file with **two modes**. Control mode (default): camera feed via getUserMedia (never OBS compositing), caption rendering, toolbar (language, aspect ratio, colors, mirror, safe zones, drag, caption width), mic-permission banner, and the mic→WebSocket streamer. Output mode (`?output=1`): chrome-free, never mirrored, auto-starts the saved camera, letterboxes the chosen ratio inside the fixed 16:9 frame, and live-syncs settings from the control window via `storage` events. Also usable standalone in Chrome or as an OBS browser source. URL params: `fontsize`, `maxlines`, `fadems` (default 25000; `0` = never fade), `bg`, `port`, `output`.

**Virtual camera** (`virtual_cam.py`): receives `[4-byte BE length][JPEG]` frames over TCP and pushes them via pyvirtualcam to the **OBS Camera Extension** (macOS) / OBS DirectShow filter (Windows). OBS Studio 28+ must be installed for the driver; main.js detects it (app bundle on macOS, registry on Windows) and falls back to a captions-only dialog if missing — there is no privileged install step. `vcam/` contains an experimental in-house Mach-server dylib (unused).

**HARD DRIVER CONSTRAINT — camera geometry is fixed 1280×720.** Empirically verified (2026-07-05): pyvirtualcam will create the OBS Camera Extension camera at other sizes (e.g. 720×1280 portrait) and consumers even enumerate it, but the extension logs "Pixel buffer size mismatch" and delivers **blank buffers** — apps see a dead feed. Do not resize the camera; non-16:9 ratios are composed *inside* the frame (output-window CSS letterbox; phone viewers in fill mode crop the bars). Lifting this requires shipping our own CMIOExtension driver.

## Platform gotchas

- **Packaged app depends on the repo checkout**: the DMG is a shell; it does not bundle Python. `electron/main.js` resolves the engine root as `LAOLAO_ROOT` env → `root` in `<userData>/settings.json` → `~/code/Laolao`, validating that `server.py` and the venv python exist (a recovery dialog with "Choose Folder…" persists a valid path). Dev mode always uses `__dirname/..` with no validation.
- Packaged-app extraResources resolve via `process.resourcesPath`, not `__dirname` (which points inside app.asar).
- On Windows, main.js resolves the OBS bin dir from the registry (`HKLM\SOFTWARE\OBS Studio`, hardcoded-path fallback) and prepends it to PATH so pyvirtualcam can load the OBS DirectShow DLL.
- The hidden output window relies on `paintWhenInitiallyHidden` + disabled background throttling — without these, `capturePage()` returns black frames for a never-shown/occluded window and the virtual camera goes dark. Verified on macOS/Electron 29; re-verify on Windows.
- Temp/log paths must use `tempfile.gettempdir()`, not `/tmp` (doesn't exist on Windows).
- The virtual camera requires a GUI session — it does not work over SSH.
- Only one producer can feed "OBS Virtual Camera" at a time (OBS Studio's own virtual camera conflicts with Laolao's).

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
| `t2s` | `true` | Traditional→Simplified conversion for zh/yue (set `false` for HK/TW Traditional readers) |
| `ws_port` | `8765` | WebSocket port (CLI flag is `--port`, not `--ws-port`) |
| `mic_device` | `null` | Mic index/name substring; null = system default (standalone mode only) |

## Testing notes

- `tests/fixtures/` is gitignored — must be generated locally before inference tests run
- Markers: `@pytest.mark.slow` (requires model download), `@pytest.mark.macos_only`
- `conftest.py` provides shared pytest fixtures for backend, VAD, and audio samples
- `test_utterance_processor.py` unit-tests the caption engine with fake backend/VAD (segment-commit, clear_partial, queue path, t2s) — no model needed
- `test_callapp_compat_macos.py` verifies the virtual camera enumerates via AVFoundation (what WeChat's picker uses) and that pixels survive to a consumer; results and manual per-app procedures live in `docs/COMPAT.md`
- `test_virtualcam_macos.py` / `test_windows_headless.py` / `test_mic_e2e_windows.py` are e2e diagnostics that exercise the real camera/mic pipeline, not just units
- Latency target: ~500–800ms partial, ~1.2s final on Apple Silicon with `base` model
