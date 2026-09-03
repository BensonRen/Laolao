"""Abstract base class for all transcription backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class BaseBackend(ABC):
    """
    Contract all backends must satisfy.

    Audio format: int16 numpy array, mono, 16 000 Hz sample rate.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    @abstractmethod
    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        beam_size: int | None = None,
    ) -> str:
        """
        Transcribe *audio* (int16, 16 kHz mono) and return plain text.

        Args:
            audio:     1-D int16 numpy array of raw PCM samples at 16 kHz.
            language:  BCP-47 language hint, e.g. "zh", "en", "yue".
                       None means auto-detect.
            beam_size: Override the backend's configured beam width for this one
                       call. 1 is greedy. None uses the configured default.
                       Lets the caller spend accuracy on finals and latency on
                       partials without building two backends.

        Returns:
            Transcribed text, stripped of leading/trailing whitespace.
            Empty string if nothing was recognised.
        """
        ...

    @classmethod
    def is_available(cls) -> bool:
        """Return True if this backend's dependencies are installed."""
        return True

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def __repr__(self) -> str:
        return f"<{self.name}>"
