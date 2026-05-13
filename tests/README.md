# Laolao Test Suite

Tests for the [Laolao (老老)](../README.md) real-time Chinese speech caption tool.

## Prerequisites

```bash
pip install pytest numpy
# For backend tests:
pip install faster-whisper
```

## Running tests

### All tests (fast + slow)

```bash
pytest tests/
```

### Only fast tests (no Whisper inference)

```bash
pytest tests/ -m "not slow"
```

### Only slow tests (requires faster-whisper tiny model)

```bash
pytest tests/ -m slow -v -s
```

### Only macOS-specific tests

```bash
pytest tests/ -m macos_only -v -s
```

### Latency benchmarks (prints timing info)

```bash
pytest tests/test_latency.py -v -s
```

## Generating test audio fixtures

The `tests/fixtures/` directory contains generated WAV files used by some tests.
This directory is **gitignored** — generate fixtures locally before running
audio-dependent tests.

### Synthetic fixtures (silence + sine tone, no internet needed)

```bash
python tests/generate_test_audio.py
# Optional: specify a custom output directory
python tests/generate_test_audio.py --output-dir /tmp/my_fixtures
```

This creates:
- `tests/fixtures/silence_2s.wav` — 2 seconds of silence
- `tests/fixtures/tone_440hz_1s.wav` — 1 second 440 Hz sine tone
- `tests/fixtures/chinese_speech.wav` — macOS TTS Mandarin speech *(macOS only)*
- `tests/fixtures/english_speech.wav` — macOS TTS English speech *(macOS only)*

macOS TTS requires a Mandarin voice (Tingting or Meijia).  Install via
**System Settings → Accessibility → Spoken Content → System Voice → Manage Voices**.

### Downloading YouTube audio

```bash
# Install dependencies first
pip install yt-dlp
brew install ffmpeg  # macOS
# sudo apt install ffmpeg  # Ubuntu

python tests/download_audio.py --url "https://www.youtube.com/watch?v=YOUR_URL" \
    --output-name chinese_news.wav \
    --duration 60
```

The script clips to the first `--duration` seconds and converts to 16 kHz mono WAV.

## Test structure

| File | Purpose |
|------|---------|
| `conftest.py` | Shared fixtures and marker registration |
| `generate_test_audio.py` | Synthetic WAV generation (silence, tones, macOS TTS) |
| `download_audio.py` | YouTube audio downloader via yt-dlp |
| `test_backends.py` | Unit + integration tests for transcription backends |
| `test_vad.py` | Unit tests for VAD implementations |
| `test_latency.py` | Latency benchmarking tests |
| `fixtures/` | Generated test WAV files (gitignored, not committed) |

## Markers

| Marker | Meaning |
|--------|---------|
| `slow` | Runs Whisper inference; may take 5–30 s on CPU |
| `macos_only` | Requires macOS tools (`say`, macOS TTS voices) |

## CI notes

- CI should run `pytest tests/ -m "not slow"` to keep build times short.
- The `slow` tests are intended for local development and pre-release validation.
- The `backend` fixture auto-skips if faster-whisper is not installed.
