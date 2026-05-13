# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Laolao (老老) is a fully offline real-time speech-to-text captioning tool for video calls, optimized for Chinese. It streams captions to an OBS browser source overlay using local Whisper inference — no cloud APIs.

## Commands

```bash
# Setup
./setup.sh                          # Creates venv, installs deps (auto-detects macOS/CUDA)

# Run
./run.sh                            # Start server with config.json defaults
./run.sh --model small --language yue
./run.sh --list-devices             # List microphone indices
./run.sh --benchmark                # Measure transcription latency

# Tests
pytest tests/                               # All tests
pytest tests/ -m "not slow"                 # Skip Whisper inference tests
pytest tests/ -m slow -v -s                 # Inference tests (needs tiny model downloaded)
pytest tests/test_latency.py -v -s          # Latency benchmarks

# Generate test fixtures before running tests
python tests/generate_test_audio.py
python tests/download_audio.py --url "<youtube_url>" --duration 60
```

## Architecture

**Data flow:**
```
Microphone → sounddevice (16kHz mono f32) → audio queue
  → UtteranceProcessor (VAD + rolling window) → Whisper backend
  → WebSocket broadcast → overlay/index.html (OBS browser source)
```

**server.py** is the entire application — audio capture thread feeds a queue, the main asyncio loop consumes it via `UtteranceProcessor`, and WebSocket clients (OBS) receive JSON messages `{"type": "partial"|"final", "text": "..."}`.

**Pluggable backends** (`backends/`): auto-selects MLX (Apple Silicon) → CUDA faster-whisper → CPU faster-whisper. Each backend implements `transcribe(audio: np.ndarray) -> str` and `is_available() -> bool`.

**Pluggable VAD** (`vad/`): auto-selects Silero-VAD (accurate ONNX) → EnergyVAD (RMS fallback). VAD gates audio accumulation; `silence_chunks` consecutive silent chunks trigger utterance finalization.

**Overlay** (`overlay/index.html`): standalone HTML/CSS/JS served as OBS browser source. Connects to `ws://localhost:{port}`, renders partial text in yellow and final text in white (fades after 4s). Customizable via URL params: `fontsize`, `maxlines`, `fadems`, `bg`, `port`.

## Key configuration (`config.json`)

| Key | Default | Effect |
|-----|---------|--------|
| `model` | `base` | tiny/base/small/medium/large-v3 |
| `language` | `zh` | zh/yue/en/ja/ko/auto |
| `device` | `auto` | auto/cpu/cuda/mlx |
| `chunk_ms` | `300` | Audio chunk size — lower = lower latency, higher CPU |
| `rolling_window_s` | `4.0` | Max audio fed to Whisper per pass |
| `silence_chunks` | `4` | Silent chunks before finalizing utterance |
| `partial_interval_s` | `0.5` | Frequency of partial text updates during speech |

## Testing notes

- `tests/fixtures/` is gitignored — must be generated locally before inference tests run
- Markers: `@pytest.mark.slow` (requires model download), `@pytest.mark.macos_only`
- `conftest.py` provides shared pytest fixtures for backend, VAD, and audio samples
- Latency target: ~500–800ms partial, ~1.2s final on Apple Silicon with `base` model
