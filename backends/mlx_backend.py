"""MLX Whisper backend — Apple Silicon (M1/M2/M3/M4) Neural Engine acceleration."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from backends.base import BaseBackend

log = logging.getLogger("laolao.backends.mlx")

# Maps Laolao model size names to HuggingFace MLX Community repos.
_MODEL_REPOS: dict[str, str] = {
    "tiny":           "mlx-community/whisper-tiny-mlx",
    "base":           "mlx-community/whisper-base-mlx",
    "small":          "mlx-community/whisper-small-mlx",
    "medium":         "mlx-community/whisper-medium-mlx",
    "large-v3":       "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
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
        # Never substitute silently. The old default-to-base fallback meant that
        # asking for large-v3-turbo on a Mac quietly ran `base` instead, and the
        # only evidence was the transcription being worse.
        if model_size not in _MODEL_REPOS:
            raise ValueError(
                f"no MLX Whisper export mapped for model {model_size!r}; "
                f"available: {sorted(_MODEL_REPOS)}"
            )
        hf_repo = _MODEL_REPOS[model_size]

        # beam_size 1 is greedy. >1 cannot be delegated to mlx-whisper -- its
        # decoder raises NotImplementedError for beam search -- so it routes
        # through backends/mlx_beam.py, which drives the same shared search the
        # Snapdragon and ONNX lanes use.
        self.beam_size = max(1, int(cfg.get("beam_size", 1)))
        self.length_penalty = float(cfg.get("length_penalty", 1.0))
        self.max_new_tokens = int(cfg.get("max_new_tokens", 180))
        self._beam_decoder = None
        self._beam_broken = False
        self._warned_auto = False

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

    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        beam_size: int | None = None,
    ) -> str:
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

        beams = self.beam_size if beam_size is None else max(1, int(beam_size))
        # Beam search forces a language token into the prompt, so it cannot serve
        # "auto". mlx-whisper's own greedy path does real language detection;
        # falling back to it is correct, where guessing English would be wrong on
        # every non-English call.
        if beams > 1 and language in (None, "auto"):
            if not self._warned_auto:
                self._warned_auto = True
                log.info("language=auto: decoding greedily so the language can be "
                         "detected. Set an explicit language to use beam search.")
            beams = 1
        if beams > 1 and not self._beam_broken:
            text = self._beam_transcribe(audio_f32, language, beams)
            if text is not None:
                return text

        initial_prompt = "以下是普通话的句子。" if language == "zh" else None
        result = mlx_whisper.transcribe(
            audio_f32,
            path_or_hf_repo=self._hf_repo,
            language=language,
            initial_prompt=initial_prompt,
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

    def _beam_transcribe(
        self, audio_f32: np.ndarray, language: str | None, beams: int
    ) -> str | None:
        """Beam-decode via backends/mlx_beam.py, or None to fall back to greedy.

        The beam path reaches into mlx-whisper's model internals (its KV-cache
        layout, its tokenizer specials), which are not a stable public API. If a
        future mlx-whisper reshapes them, captions must degrade to greedy rather
        than stop: a slightly worse caption is a usable tool, an exception on
        every utterance is not. The failure is logged once, not once per
        utterance, so it stays visible without flooding a live call's log.
        """
        try:
            if self._beam_decoder is None:
                from backends.mlx_beam import MlxBeamDecoder

                self._beam_decoder = MlxBeamDecoder(self._hf_repo)
                log.info("MLX beam search ready (beam_size=%d).", beams)
            return self._beam_decoder.transcribe(
                audio_f32,
                language,
                beams,
                length_penalty=self.length_penalty,
                max_new_tokens=self.max_new_tokens,
            )
        except Exception:
            self._beam_broken = True
            log.exception(
                "MLX beam search failed; falling back to greedy mlx-whisper "
                "for the rest of this session."
            )
            return None
