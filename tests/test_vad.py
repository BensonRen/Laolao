"""Tests for Voice Activity Detection (VAD) implementations."""

from __future__ import annotations

import sys
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

def _silence(n_samples: int = 4800) -> np.ndarray:
    """Return int16 silence (zeros)."""
    return np.zeros(n_samples, dtype=np.int16)


def _loud_noise(n_samples: int = 4800, amplitude: int = 28000) -> np.ndarray:
    """Return loud int16 white noise well above any reasonable RMS threshold."""
    rng = np.random.default_rng(seed=7)
    return rng.integers(-amplitude, amplitude, size=n_samples, dtype=np.int16)


def _make_energy_vad(silence_rms: float = 0.008):
    """Instantiate EnergyVAD with the given threshold."""
    from vad.energy_vad import EnergyVAD  # noqa: PLC0415
    return EnergyVAD(cfg={"silence_rms": silence_rms})


# ---------------------------------------------------------------------------
# EnergyVAD tests
# ---------------------------------------------------------------------------

def test_energy_vad_silence_returns_false() -> None:
    """Silence (zeros) does not trigger speech detection."""
    vad = _make_energy_vad()
    # Feed three chunks of silence to fill the history buffer.
    for _ in range(3):
        result = vad.is_speech(_silence())
    assert result is False, "EnergyVAD falsely detected speech in silence"


def test_energy_vad_loud_noise_triggers_true() -> None:
    """Loud noise (high amplitude) triggers speech detection."""
    vad = _make_energy_vad(silence_rms=0.008)
    # Feed three loud chunks to fill the history majority threshold.
    for _ in range(3):
        result = vad.is_speech(_loud_noise())
    assert result is True, "EnergyVAD failed to detect loud audio above threshold"


def test_energy_vad_reset() -> None:
    """After reset(), VAD history is cleared so silence is not detected as speech."""
    vad = _make_energy_vad()

    # Trigger detection with loud noise.
    for _ in range(3):
        vad.is_speech(_loud_noise())

    # Reset should clear the history.
    vad.reset()

    # After reset + one silent chunk the history should be mostly False.
    # (history is all-False immediately after reset; first is_speech call
    #  appends one False and pops one False → still all-False)
    result = vad.is_speech(_silence())
    assert result is False, (
        "EnergyVAD still returned True after reset() followed by silence"
    )


def test_energy_vad_threshold_configurable() -> None:
    """Custom silence_rms threshold is respected.

    A very high threshold (0.99) means even loud noise should NOT trigger speech,
    while a very low threshold (0.0001) means even a quiet signal should trigger.
    """
    # Very high threshold — nothing should be speech.
    vad_high = _make_energy_vad(silence_rms=0.99)
    for _ in range(3):
        result_high = vad_high.is_speech(_loud_noise())
    assert result_high is False, (
        "EnergyVAD with threshold=0.99 should not detect noise as speech"
    )

    # Very low threshold — even modest noise should be speech.
    vad_low = _make_energy_vad(silence_rms=1e-6)
    # Feed moderately low-amplitude signal (RMS should still exceed 1e-6).
    modest_noise = (np.random.default_rng(42).integers(-500, 500, 4800)).astype(np.int16)
    for _ in range(3):
        result_low = vad_low.is_speech(modest_noise)
    assert result_low is True, (
        "EnergyVAD with threshold=1e-6 should detect even modest noise as speech"
    )


def test_energy_vad_history_majority_rule() -> None:
    """EnergyVAD uses a majority vote over the last 3 chunks.

    One silent chunk followed by two loud chunks → True.
    Two silent chunks followed by one loud chunk → False.
    """
    vad = _make_energy_vad(silence_rms=0.008)

    # Pattern: silence, loud, loud → majority is loud → True
    vad.reset()
    vad.is_speech(_silence())
    vad.is_speech(_loud_noise())
    result = vad.is_speech(_loud_noise())
    assert result is True, "Expected True with 2/3 loud chunks"

    # Pattern: loud, silence, silence → majority is silence → False
    vad.reset()
    vad.is_speech(_loud_noise())
    vad.is_speech(_silence())
    result = vad.is_speech(_silence())
    assert result is False, "Expected False with 2/3 silent chunks"


def test_energy_vad_is_available() -> None:
    """EnergyVAD.is_available() always returns True (no extra dependencies)."""
    from vad.energy_vad import EnergyVAD
    assert EnergyVAD.is_available() is True


def test_energy_vad_name() -> None:
    """EnergyVAD.name property returns the class name string."""
    from vad.energy_vad import EnergyVAD
    vad = EnergyVAD(cfg={})
    assert vad.name == "EnergyVAD"


# ---------------------------------------------------------------------------
# SileroVAD tests (import / availability only — no inference required)
# ---------------------------------------------------------------------------

def test_silero_vad_available_import() -> None:
    """If silero-vad is installed, SileroVAD.is_available() returns True."""
    silero_vad_pkg = pytest.importorskip(
        "silero_vad",
        reason="silero-vad not installed — skipping SileroVAD tests",
    )
    from vad.silero_vad import SileroVAD
    assert SileroVAD.is_available() is True


def test_silero_vad_not_available_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """SileroVAD.is_available() returns False when silero_vad cannot be imported."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "silero_vad":
            raise ImportError("mocked missing silero_vad")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Re-import to exercise is_available() with the patched import.
    import importlib
    import vad.silero_vad as silero_module
    importlib.reload(silero_module)

    from vad.silero_vad import SileroVAD
    assert SileroVAD.is_available() is False


# ---------------------------------------------------------------------------
# BaseVAD interface contract
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SileroOnnxVAD — the torch-free path (real inference)
# ---------------------------------------------------------------------------

def _onnx_silero_or_skip():
    """Build a SileroOnnxVAD, or skip if onnxruntime/weights are unavailable.

    Never downloads inside a test: CI and offline runs must not depend on the
    network, so a missing cache is a skip rather than a fetch.
    """
    pytest.importorskip("onnxruntime", reason="onnxruntime not installed")
    from vad.silero_vad import SileroOnnxVAD  # noqa: PLC0415
    if not SileroOnnxVAD.model_path({}).exists():
        pytest.skip("silero_vad.onnx not cached — run Laolao once to fetch it")
    return SileroOnnxVAD(cfg={})


def _noise_dbfs(dbfs: float, n: int = 4800, seed: int = 3) -> np.ndarray:
    """int16 gaussian noise whose RMS sits at *dbfs* relative to full scale."""
    rng = np.random.default_rng(seed)
    rms = (10.0 ** (dbfs / 20.0)) * 32768.0
    return np.clip(rng.standard_normal(n) * rms, -32768, 32767).astype(np.int16)


def test_silero_onnx_rejects_noise_at_every_level() -> None:
    """The whole reason this backend exists.

    EnergyVAD measures loudness, so steady noise above about -40 dBFS reads as
    speech and Whisper, handed non-speech, invents captions. A neural VAD must
    stay quiet no matter how loud the noise is.
    """
    vad = _onnx_silero_or_skip()
    for dbfs in (-55, -40, -30, -20):
        vad.reset()
        fired = [vad.is_speech(_noise_dbfs(dbfs)) for _ in range(6)]
        assert not any(fired), f"called steady {dbfs} dBFS noise speech: {fired}"


def test_silero_onnx_detects_real_speech() -> None:
    """Synthetic audio cannot validate a speech detector — use a real fixture."""
    vad = _onnx_silero_or_skip()
    wav = PROJECT_ROOT / "tests" / "fixtures" / "english_speech.wav"
    if not wav.exists():
        pytest.skip("run tests/generate_test_audio.py to create fixtures")

    import wave  # noqa: PLC0415
    with wave.open(str(wav), "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    vad.reset()
    chunk = 4800  # 300 ms, the pipeline's default
    fired = [vad.is_speech(audio[i:i + chunk])
             for i in range(0, len(audio) - chunk, chunk)]
    assert any(fired), "did not detect speech anywhere in a speech fixture"


def test_silero_onnx_context_is_threaded_between_windows() -> None:
    """Guards the subtlest bug in this backend.

    v5 needs the last 64 samples of the previous window prepended to the next.
    Omit it and the model still runs and still returns probabilities — they are
    just far too low, so speech is silently never detected. Feeding the same
    speech twice must not produce a *lower* score the second time, which is
    what a mis-threaded context looks like.
    """
    vad = _onnx_silero_or_skip()
    from vad.silero_vad import _CONTEXT_SAMPLES  # noqa: PLC0415
    assert _CONTEXT_SAMPLES == 64

    vad.reset()
    assert vad._context.shape == (64,)
    assert not vad._context.any(), "context must start zeroed"

    window = _noise_dbfs(-20, n=512)
    vad._probability(window.astype(np.float32) / 32768.0)
    assert vad._context.any(), "context was not carried after a window"


def test_silero_onnx_reset_clears_state() -> None:
    vad = _onnx_silero_or_skip()
    wav = PROJECT_ROOT / "tests" / "fixtures" / "english_speech.wav"
    if not wav.exists():
        pytest.skip("run tests/generate_test_audio.py to create fixtures")
    import wave  # noqa: PLC0415
    with wave.open(str(wav), "rb") as wf:
        audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    for i in range(0, min(len(audio), 4800 * 4), 4800):
        vad.is_speech(audio[i:i + 4800])
    vad.reset()
    assert vad._speaking is False
    assert not vad._state.any(), "LSTM state must be zeroed by reset()"
    assert not vad._context.any(), "context must be zeroed by reset()"


def test_base_vad_reset_default_noop() -> None:
    """BaseVAD.reset() is a no-op by default (EnergyVAD overrides it)."""
    from vad.base import BaseVAD

    class MinimalVAD(BaseVAD):
        def is_speech(self, chunk: np.ndarray) -> bool:
            return False

    vad = MinimalVAD(cfg={})
    # Should not raise.
    vad.reset()


def test_base_vad_is_available_default_true() -> None:
    """BaseVAD.is_available() returns True by default."""
    from vad.base import BaseVAD

    class MinimalVAD(BaseVAD):
        def is_speech(self, chunk: np.ndarray) -> bool:
            return False

    assert MinimalVAD.is_available() is True
