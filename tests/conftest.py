"""pytest fixtures and configuration for the Laolao test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Make the project root importable when running pytest from any directory.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Custom markers
# ---------------------------------------------------------------------------

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "slow: marks tests that run Whisper inference (may take tens of seconds on CPU)",
    )
    config.addinivalue_line(
        "markers",
        "macos_only: marks tests that require macOS-specific tools (e.g. `say` TTS)",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def backend_cfg() -> dict:
    """Minimal config suitable for faster-whisper tiny model on CPU."""
    return {
        "model": "tiny",
        "language": "zh",
        "device": "cpu",
        "compute_type": "int8",
    }


@pytest.fixture(scope="session")
def backend(backend_cfg: dict):
    """
    A live FasterWhisperBackend instance for integration tests.

    Skipped automatically if faster-whisper is not installed or the tiny
    model cannot be loaded (e.g. no internet during first-time download).
    """
    faster_whisper = pytest.importorskip(
        "faster_whisper",
        reason="faster-whisper not installed — skipping backend fixture",
    )
    from backends.faster_whisper_backend import FasterWhisperBackend  # noqa: PLC0415
    try:
        be = FasterWhisperBackend(backend_cfg)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Could not load faster-whisper tiny model: {exc}")
    return be


@pytest.fixture(scope="session")
def energy_vad():
    """An EnergyVAD instance with default config."""
    from vad.energy_vad import EnergyVAD  # noqa: PLC0415
    return EnergyVAD(cfg={})


@pytest.fixture
def sample_audio_silence() -> np.ndarray:
    """1 second of int16 silence (zeros) at 16 kHz."""
    return np.zeros(16000, dtype=np.int16)


@pytest.fixture
def sample_audio_noise() -> np.ndarray:
    """1 second of int16 white noise at 16 kHz with high amplitude."""
    rng = np.random.default_rng(seed=42)
    # Use 80% of int16 range so RMS is well above any reasonable threshold.
    samples = rng.integers(-26000, 26000, size=16000, dtype=np.int16)
    return samples


# ---------------------------------------------------------------------------
# Skip helper
# ---------------------------------------------------------------------------

def _tiny_model_path() -> Path | None:
    """Return the cached model path if the tiny model has been downloaded."""
    cache_dir = Path.home() / ".cache" / "laolao" / "models"
    # faster-whisper stores models as directories named like "Systran/faster-whisper-tiny"
    candidates = list(cache_dir.glob("**/tiny*")) + list(cache_dir.glob("**/faster*tiny*"))
    return candidates[0] if candidates else None


skip_if_no_model = pytest.mark.skipif(
    _tiny_model_path() is None,
    reason="faster-whisper tiny model not found in ~/.cache/laolao/models — "
           "run the backend once to download it",
)
