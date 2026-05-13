"""faster-whisper backend (Linux, Windows, Intel Mac, NVIDIA GPU)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from backends.base import BaseBackend

log = logging.getLogger("laolao.backends.faster_whisper")


class FasterWhisperBackend(BaseBackend):
    """
    Uses SYSTRAN/faster-whisper — a CTranslate2-optimised Whisper port.

    Runs on CPU (int8) or NVIDIA CUDA (float16).
    ~4x faster than the original openai-whisper on CPU.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        from faster_whisper import WhisperModel

        model_size = cfg.get("model", "base")
        device = cfg.get("device", "cpu")
        compute_type = cfg.get("compute_type", "int8")
        cache_dir = str(Path.home() / ".cache" / "laolao" / "models")

        log.info("Loading faster-whisper '%s' on %s (%s)…",
                 model_size, device, compute_type)
        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=cache_dir,
        )
        log.info("faster-whisper ready.")

    # Biases Whisper toward Simplified characters when language is "zh".
    _ZH_SIMPLIFIED_PROMPT = "以下是普通话的句子。"

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> str:
        if len(audio) < 1600:          # < 100 ms — skip
            return ""
        audio_f32 = audio.astype(np.float32) / 32768.0
        initial_prompt = self._ZH_SIMPLIFIED_PROMPT if language == "zh" else None
        segments, _ = self._model.transcribe(
            audio_f32,
            language=language,
            initial_prompt=initial_prompt,
            beam_size=1,
            best_of=1,
            temperature=0,
            condition_on_previous_text=False,
            vad_filter=False,          # Laolao does its own VAD
            without_timestamps=True,
        )
        return "".join(s.text for s in segments).strip()

    @classmethod
    def is_available(cls) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False
