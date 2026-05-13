"""Abstract base class for all VAD implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class BaseVAD(ABC):
    """
    Voice Activity Detector contract.

    Input: 1-D int16 numpy array at 16 000 Hz.
    Output: bool — True if this chunk contains speech.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    @abstractmethod
    def is_speech(self, chunk: np.ndarray) -> bool:
        """Return True if *chunk* (int16, 16 kHz) likely contains speech."""
        ...

    def reset(self) -> None:
        """Reset any internal state. Called at utterance boundaries."""
        pass

    @classmethod
    def is_available(cls) -> bool:
        return True

    @property
    def name(self) -> str:
        return self.__class__.__name__
