"""Tests for transcription backends.

All tests that actually run Whisper inference are marked ``slow`` and
require the faster-whisper tiny model to be downloaded.  Tests that only
check availability / imports run in a few milliseconds.
"""

from __future__ import annotations

import platform
import re
import sys
import time
from pathlib import Path

import numpy as np
import pytest

# Ensure project root is importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_silence(n_samples: int = 48000) -> np.ndarray:
    """Return n_samples of int16 silence."""
    return np.zeros(n_samples, dtype=np.int16)


def _make_noise(n_samples: int = 48000, seed: int = 0) -> np.ndarray:
    """Return n_samples of moderately loud white noise (int16)."""
    rng = np.random.default_rng(seed)
    return rng.integers(-20000, 20000, size=n_samples, dtype=np.int16)


# Chinese Unicode block: CJK Unified Ideographs U+4E00–U+9FFF
_CJK_PATTERN = re.compile(r"[一-鿿]")


def _contains_chinese(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


# ---------------------------------------------------------------------------
# Availability / import tests (fast — no inference)
# ---------------------------------------------------------------------------

def test_faster_whisper_backend_available() -> None:
    """FasterWhisperBackend.is_available() returns True when installed."""
    faster_whisper = pytest.importorskip(
        "faster_whisper",
        reason="faster-whisper not installed",
    )
    from backends.faster_whisper_backend import FasterWhisperBackend
    assert FasterWhisperBackend.is_available() is True


def test_base_backend_interface() -> None:
    """BaseBackend defines the expected abstract interface."""
    from backends.base import BaseBackend
    assert hasattr(BaseBackend, "transcribe")
    assert hasattr(BaseBackend, "is_available")
    assert hasattr(BaseBackend, "name")


def test_backend_repr(backend) -> None:
    """Backend __repr__ includes the class name."""
    assert "FasterWhisperBackend" in repr(backend)


# ---------------------------------------------------------------------------
# Inference tests (slow — require tiny model)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_backend_transcribes_silence_returns_empty(backend) -> None:
    """Transcribing 3 seconds of silence returns an empty or near-empty string."""
    silence = _make_silence(n_samples=3 * 16000)
    result = backend.transcribe(silence, language="zh")
    assert isinstance(result, str)
    # Whisper may hallucinate a few characters on silence; tolerate up to 10.
    assert len(result.strip()) <= 10, (
        f"Expected near-empty transcription for silence, got: {result!r}"
    )


@pytest.mark.slow
def test_backend_transcribes_returns_string(backend) -> None:
    """transcribe() always returns a str."""
    audio = _make_noise(n_samples=16000)
    result = backend.transcribe(audio, language="zh")
    assert isinstance(result, str)


@pytest.mark.slow
def test_backend_handles_short_audio(backend) -> None:
    """Audio shorter than 100 ms (1600 samples) returns empty string without error."""
    short_audio = np.zeros(800, dtype=np.int16)   # 50 ms — below the 100 ms guard
    result = backend.transcribe(short_audio, language="zh")
    assert result == "", f"Expected empty string for sub-100ms audio, got: {result!r}"


@pytest.mark.slow
@pytest.mark.macos_only
def test_backend_chinese_tts_contains_chinese(backend, tmp_path: Path) -> None:
    """Transcribing macOS-generated Chinese TTS contains at least one Chinese character."""
    if platform.system() != "Darwin":
        pytest.skip("macOS required for `say` TTS generation")

    from tests.generate_test_audio import generate_chinese_tts_macos, load_wav

    wav_path = tmp_path / "chinese_tts.wav"
    ok = generate_chinese_tts_macos("你好，奶奶。我今天很想念你。", wav_path)
    if not ok:
        pytest.skip("No Mandarin/Cantonese voice available on this macOS installation")

    audio, sr = load_wav(wav_path)
    result = backend.transcribe(audio, language="zh")

    assert isinstance(result, str)
    assert _contains_chinese(result), (
        f"Expected at least one Chinese character in transcription of Chinese TTS, "
        f"got: {result!r}"
    )


@pytest.mark.slow
def test_backend_benchmark_timing(backend) -> None:
    """Transcription of 3 s audio completes in under 30 s on CPU (sanity check)."""
    audio = _make_silence(n_samples=3 * 16000)

    start = time.perf_counter()
    result = backend.transcribe(audio, language="zh")
    elapsed = time.perf_counter() - start

    assert isinstance(result, str)
    assert elapsed < 30.0, (
        f"Transcription took {elapsed:.1f}s which exceeds the 30s safety limit. "
        f"Check that faster-whisper tiny is running on CPU (int8)."
    )
    print(f"\n[timing] 3s audio → {elapsed:.2f}s wall-clock on CPU")


@pytest.mark.slow
def test_backend_language_none_auto_detect(backend) -> None:
    """Passing language=None does not raise an error (auto-detect mode)."""
    silence = _make_silence(n_samples=2 * 16000)
    result = backend.transcribe(silence, language=None)
    assert isinstance(result, str)
