"""Lightweight energy-based VAD — no extra dependencies."""

from __future__ import annotations

import numpy as np

from vad.base import BaseVAD


class EnergyVAD(BaseVAD):
    """
    Simple RMS-energy voice activity detector.

    Fast (<1 µs per chunk), zero dependencies beyond numpy.
    Less accurate than Silero for noisy environments or quiet speech.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._threshold = float(cfg.get("silence_rms", 0.008))
        self._history_len = 3
        self._history: list[bool] = [False] * self._history_len

    def is_speech(self, chunk: np.ndarray) -> bool:
        # Normalise to [-1, 1] so threshold is in the same domain as config
        rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768.0) ** 2)))
        active = rms > self._threshold
        self._history.append(active)
        self._history.pop(0)
        return sum(self._history) >= (self._history_len // 2 + 1)

    def reset(self) -> None:
        self._history = [False] * self._history_len
