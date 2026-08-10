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

### Standard fixtures

```bash
python tests/generate_test_audio.py
# Optional: specify a custom output directory
python tests/generate_test_audio.py --output-dir /tmp/my_fixtures
# Never touch the network (skips the Mandarin fallback clip)
python tests/generate_test_audio.py --no-download
```

This creates:
- `tests/fixtures/silence_2s.wav` — 2 seconds of silence
- `tests/fixtures/tone_440hz_1s.wav` — 1 second 440 Hz sine tone
- `tests/fixtures/english_speech.wav` — short English utterance (system TTS)
- `tests/fixtures/en_long_speech.wav` — ~8 s English utterance for latency/streaming
- `tests/fixtures/chinese_speech.wav` — Mandarin speech

Speech is synthesised with the platform's TTS: macOS `say`, or Windows SAPI
(`System.Speech.Synthesis.SpeechSynthesizer`, writing 16 kHz / 16-bit / mono
directly). Every speech WAV is written with a ground-truth `<stem>.txt` holding
only its transcript — that is what `docs/snapdragon/acceptance/check.py` (A2/A3)
compares against. Provenance goes in `<stem>.source.json` and `fixtures/README.md`,
and every generated WAV is read back and validated (rate, channels, bit depth,
duration, peak amplitude) before the script reports success.

macOS Mandarin TTS requires a Mandarin voice (Tingting or Meijia).  Install via
**System Settings → Accessibility → Spoken Content → System Voice → Manage Voices**.
Windows ships no Chinese SAPI voice without a language pack; when none is found the
generator downloads an openly-licensed Mandarin clip (AISHELL-1, Apache-2.0) together
with the corpus's own published transcript. It never invents a transcript.

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
| `generate_test_audio.py` | Fixture WAV generation (silence, tones, macOS `say` / Windows SAPI TTS, ground truth) |
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
