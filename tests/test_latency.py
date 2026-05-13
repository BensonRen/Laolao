"""Latency benchmarking tests for Laolao transcription pipeline.

These tests measure wall-clock time and assert conservative upper bounds
so they pass even on slow CI machines.  They are all marked ``slow``
because they require the faster-whisper tiny model.

Run only these tests:
    pytest tests/test_latency.py -v -s
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

# Ensure project root is importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SAMPLE_RATE = 16000  # Hz


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.int16)


def _noise(seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(-8000, 8000, size=int(seconds * SAMPLE_RATE), dtype=np.int16)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_transcription_latency_baseline(backend) -> None:
    """
    Measure and report transcription latency for the tiny model on CPU.

    This is a sanity-check test with a very conservative 10 s upper bound
    so it passes on slow hardware.  Real-world performance on modern hardware
    is typically 0.3–1 s for a 3 s clip.
    """
    audio = _silence(seconds=3.0)

    start = time.perf_counter()
    result = backend.transcribe(audio, language="zh")
    elapsed = time.perf_counter() - start

    print(
        f"\n[latency] baseline: 3 s silence → {elapsed:.3f}s "
        f"(RTF {elapsed / 3.0:.2f}x)"
    )

    assert isinstance(result, str)
    assert elapsed < 10.0, (
        f"Transcription of 3 s audio took {elapsed:.1f}s, "
        f"exceeding the 10 s budget. "
        f"Check system load or model configuration."
    )


@pytest.mark.slow
def test_rolling_partial_latency_simulation(backend) -> None:
    """
    Simulate the rolling-partial transcription loop from server.py.

    Scenario:
    - A 500 ms audio chunk arrives every 300 ms (simulated).
    - We feed chunks into a growing rolling buffer.
    - We measure time from the first chunk until the first transcription
      result is produced.

    Asserts that the first result appears within 3 seconds of the first
    chunk being fed.  (Conservative: real target is < 1 s on modern CPUs.)
    """
    CHUNK_MS = 500           # ms per chunk
    PARTIAL_INTERVAL_S = 0.3 # how often server triggers a partial transcription
    MAX_CHUNKS = 6           # simulate 3 seconds of audio (6 × 500 ms)
    MAX_WAIT_S = 3.0         # conservative bound for first result

    chunk_samples = int(CHUNK_MS / 1000 * SAMPLE_RATE)

    # Generate chunks of mixed noise (more realistic than pure silence).
    chunks = [_noise(CHUNK_MS / 1000, seed=i) for i in range(MAX_CHUNKS)]

    rolling_buffer = np.array([], dtype=np.int16)
    first_result_elapsed: float | None = None
    wall_start = time.perf_counter()

    for i, chunk in enumerate(chunks):
        rolling_buffer = np.concatenate([rolling_buffer, chunk])

        # Transcribe the rolling buffer.
        t0 = time.perf_counter()
        result = backend.transcribe(rolling_buffer, language="zh")
        t1 = time.perf_counter()
        transcription_time = t1 - t0
        total_elapsed = t1 - wall_start

        print(
            f"\n[latency] chunk {i+1}/{MAX_CHUNKS}: "
            f"buffer={len(rolling_buffer)/SAMPLE_RATE:.1f}s, "
            f"transcription={transcription_time:.3f}s, "
            f"total_wall={total_elapsed:.3f}s, "
            f"result={result!r:.40}"
        )

        # Record when the first result (even empty) arrives.
        if first_result_elapsed is None:
            first_result_elapsed = total_elapsed

        assert isinstance(result, str), f"Expected str, got {type(result)}"

    assert first_result_elapsed is not None
    assert first_result_elapsed < MAX_WAIT_S, (
        f"First transcription result took {first_result_elapsed:.2f}s, "
        f"exceeding the {MAX_WAIT_S}s budget."
    )


@pytest.mark.slow
def test_transcription_latency_vs_audio_duration(backend) -> None:
    """
    Verify that transcription time scales reasonably with audio length.

    We transcribe 1 s, 2 s, and 3 s clips and check that no clip takes
    longer than 10 s (very conservative CPU bound).  Also prints RTF for
    each duration so the results can be read from the test output with -s.
    """
    results: list[tuple[float, float]] = []

    for duration in (1.0, 2.0, 3.0):
        audio = _silence(seconds=duration)
        t0 = time.perf_counter()
        _ = backend.transcribe(audio, language="zh")
        elapsed = time.perf_counter() - t0
        results.append((duration, elapsed))
        print(
            f"\n[latency] {duration:.0f}s audio → {elapsed:.3f}s "
            f"(RTF {elapsed/duration:.2f}x)"
        )

    for duration, elapsed in results:
        assert elapsed < 10.0, (
            f"Transcription of {duration}s audio took {elapsed:.1f}s "
            f"(limit: 10s)"
        )
