"""Silero-VAD v5 implementation of BaseVAD for Laolao.

Uses the ONNX backend so PyTorch is not required — only onnxruntime.
"""

from __future__ import annotations

import logging

import numpy as np

from .base import BaseVAD

log = logging.getLogger("laolao.vad.silero")

# Silero-VAD v5 requires chunks of exactly 512 samples at 16 kHz (32 ms).
# Feeding a different size will raise an assertion error inside the model.
# When our pipeline provides larger chunks we split them into 512-sample
# windows and return True if *any* window is detected as speech.
_SILERO_CHUNK_SAMPLES = 512


class SileroVAD(BaseVAD):
    """Voice Activity Detector backed by Silero-VAD v5 (ONNX runtime)."""

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        threshold: float = cfg.get("silero_threshold", 0.5)

        from silero_vad import load_silero_vad, VADIterator  # noqa: PLC0415

        log.debug("Loading Silero VAD model (onnx=True, threshold=%.2f)", threshold)
        self._model = load_silero_vad(onnx=True)
        self._vad_iter = VADIterator(
            self._model,
            sampling_rate=16000,
            threshold=threshold,
            min_silence_duration_ms=300,
        )
        self._speaking: bool = False
        log.info("SileroVAD ready (threshold=%.2f)", threshold)

    # ------------------------------------------------------------------
    # BaseVAD interface
    # ------------------------------------------------------------------

    def is_speech(self, chunk: np.ndarray) -> bool:
        """Return True if *chunk* (int16, 16 kHz) contains speech.

        The chunk is converted to float32 in [-1, 1] and split into
        512-sample sub-chunks required by Silero-VAD v5.  The method
        returns True if at least one sub-chunk triggers speech onset.
        """
        # Convert int16 → float32 normalised to [-1.0, 1.0]
        audio_float: np.ndarray = chunk.astype(np.float32) / 32768.0

        # Split into 512-sample windows (pad last window with zeros if needed)
        length = len(audio_float)
        for start in range(0, max(length, _SILERO_CHUNK_SAMPLES), _SILERO_CHUNK_SAMPLES):
            window = audio_float[start : start + _SILERO_CHUNK_SAMPLES]
            if len(window) < _SILERO_CHUNK_SAMPLES:
                window = np.pad(window, (0, _SILERO_CHUNK_SAMPLES - len(window)))

            result = self._vad_iter(window, return_seconds=False)

            if result is not None:
                if "start" in result:
                    log.debug("Speech start detected")
                    self._speaking = True
                elif "end" in result:
                    log.debug("Speech end detected")
                    self._speaking = False

        return self._speaking

    def reset(self) -> None:
        """Reset VADIterator state and speaking flag."""
        self._vad_iter.reset_states()
        self._speaking = False
        log.debug("SileroVAD state reset")

    @classmethod
    def is_available(cls) -> bool:
        """Return True when the silero-vad package is importable."""
        try:
            from silero_vad import load_silero_vad  # noqa: F401, PLC0415
            return True
        except ImportError:
            return False
