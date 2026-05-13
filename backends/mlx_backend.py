"""MLX Whisper backend — Apple Silicon (M1/M2/M3/M4) Neural Engine acceleration."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from backends.base import BaseBackend

log = logging.getLogger("laolao.backends.mlx")

# Maps Laolao model size names to HuggingFace MLX Community repos.
_MODEL_REPOS: dict[str, str] = {
    "tiny":     "mlx-community/whisper-tiny-mlx",
    "base":     "mlx-community/whisper-base-mlx",
    "small":    "mlx-community/whisper-small-mlx",
    "medium":   "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


class MLXBackend(BaseBackend):
    """
    Transcription backend using mlx-whisper on Apple Silicon.

    Leverages Apple's MLX framework to run Whisper on the Neural Engine /
    GPU of M1/M2/M3/M4 chips.  Models are downloaded from HuggingFace on
    first use and cached locally at ~/.cache/laolao/models/.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        model_size = cfg.get("model", "base")
        hf_repo = _MODEL_REPOS.get(model_size, _MODEL_REPOS["base"])

        cache_dir = Path.home() / ".cache" / "laolao" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)

        log.info(
            "Loading MLX Whisper model '%s' from %s (cache: %s)…",
            model_size, hf_repo, cache_dir,
        )

        # Store for use in transcribe(); mlx_whisper loads lazily on first call
        # when given a repo path, so we just keep the repo identifier.
        self._hf_repo = hf_repo

        log.info("MLX Whisper backend ready (model: %s).", model_size)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        """
        Transcribe *audio* (int16, 16 kHz mono) using MLX Whisper.

        Args:
            audio:    1-D int16 numpy array of raw PCM samples at 16 kHz.
            language: BCP-47 language hint, e.g. "zh", "en", "yue".
                      None means auto-detect.

        Returns:
            Transcribed text, stripped of leading/trailing whitespace.
            Empty string if nothing was recognised or audio is too short.
        """
        if len(audio) < 1600:  # < 100 ms — skip
            return ""

        import mlx_whisper  # noqa: PLC0415 — deferred so ImportError is local

        # Convert int16 PCM [-32768, 32767] → float32 [-1.0, 1.0]
        audio_f32 = audio.astype(np.float32) / 32768.0

        result = mlx_whisper.transcribe(
            audio_f32,
            path_or_hf_repo=self._hf_repo,
            language=language,
            verbose=False,
        )

        # mlx_whisper returns a dict: {"text": "...", "segments": [...], ...}
        if isinstance(result, dict):
            return result.get("text", "").strip()

        # Defensive: handle unexpected return types
        return str(result).strip()

    @classmethod
    def is_available(cls) -> bool:
        """Return True if mlx_whisper is installed."""
        try:
            import mlx_whisper  # noqa: F401
            return True
        except ImportError:
            return False
