"""Silero-VAD v5 implementations of BaseVAD for Laolao.

Two of them, because the obvious one is not always installable:

``SileroVAD``      drives the ``silero-vad`` pip package. Despite that package
                   offering an ONNX backend, it declares a hard dependency on
                   ``torch``, so it cannot be installed at all where torch has
                   no wheel — notably Windows on ARM64.

``SileroOnnxVAD``  runs the same ``silero_vad.onnx`` weights directly through
                   onnxruntime, with no torch and no silero-vad package. It
                   fetches the model once (~2.3 MB) and caches it beside the
                   Whisper models.

The second exists because the fallback, EnergyVAD, only measures loudness:
steady room noise above roughly −40 dBFS reads as speech, and Whisper handed
non-speech invents captions. Measured on Snapdragon X2, this model scores
noise at ≤0.12 from −55 dBFS all the way up to −20 dBFS while real speech
reaches 1.000 — which is the whole reason it is worth carrying.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from .base import BaseVAD

log = logging.getLogger("laolao.vad.silero")

# Same cache convention as the ONNX Whisper backend.
DEFAULT_MODEL_DIR = Path(
    os.environ.get(
        "LAOLAO_MODEL_DIR",
        Path(__file__).resolve().parent.parent.parent / "laolao-tools" / "models",
    )
)
SILERO_ONNX_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/"
    "src/silero_vad/data/silero_vad.onnx"
)

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


# ──────────────────────────────────────────────────────────────────────
# Torch-free path: the same weights, run directly on onnxruntime
# ──────────────────────────────────────────────────────────────────────

_CONTEXT_SAMPLES = 64          # v5 prepends 64 samples of history to each window
_STATE_SHAPE = (2, 1, 128)


class SileroOnnxVAD(BaseVAD):
    """Silero-VAD v5 via onnxruntime, with no torch and no silero-vad package.

    The model is stateful in two separate ways, and getting either wrong looks
    like a broken detector rather than an error:

    * ``state`` (2, 1, 128) is the LSTM carry, threaded through every call.
    * each 512-sample window must be prefixed with the **last 64 samples of the
      previous window**. Feed bare 512-sample windows and speech scores ~0.1
      instead of ~1.0 — it silently behaves like a VAD that never fires.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        import onnxruntime as ort  # noqa: PLC0415

        self._threshold = float(cfg.get("silero_threshold", 0.5))
        # Hysteresis: how long it must stay quiet before we call the utterance
        # over. Matches the package default so behaviour is comparable.
        min_sil_ms = float(cfg.get("silero_min_silence_ms", 300))
        self._silence_windows_needed = max(1, int(round(min_sil_ms / 32.0)))

        path = self.model_path(cfg)
        if not path.exists():
            self._download(path)

        so = ort.SessionOptions()
        # One thread: this is a 2 MB model called every 32 ms of audio, and ORT's
        # default thread pool costs more in scheduling than it saves.
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 1
        so.log_severity_level = 3
        self._sess = ort.InferenceSession(
            str(path), sess_options=so, providers=["CPUExecutionProvider"])
        self._sr = np.array(16000, dtype=np.int64)
        self.reset()
        log.info("SileroOnnxVAD ready (threshold=%.2f, min_silence=%dms)",
                 self._threshold, int(min_sil_ms))

    # ------------------------------------------------------------------

    @staticmethod
    def model_path(cfg: dict | None = None) -> Path:
        base = Path((cfg or {}).get("model_dir") or DEFAULT_MODEL_DIR)
        return base / "silero" / "silero_vad.onnx"

    @staticmethod
    def _download(path: Path) -> None:
        """Fetch the weights once. Short timeout so an offline box degrades fast."""
        import urllib.request  # noqa: PLC0415
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading Silero VAD model → %s", path)
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(SILERO_ONNX_URL, timeout=15) as r:
            tmp.write_bytes(r.read())
        tmp.replace(path)          # atomic: never leave a half file looking valid
        log.info("Silero VAD model cached (%d bytes)", path.stat().st_size)

    def _probability(self, window: np.ndarray) -> float:
        x = np.concatenate([self._context, window]).reshape(1, -1).astype(np.float32)
        out = self._sess.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._state = out[1]
        self._context = window[-_CONTEXT_SAMPLES:]
        return float(np.asarray(out[0]).reshape(-1)[0])

    # ------------------------------------------------------------------
    # BaseVAD interface
    # ------------------------------------------------------------------

    def is_speech(self, chunk: np.ndarray) -> bool:
        audio = chunk.astype(np.float32) / 32768.0
        for start in range(0, max(len(audio), _SILERO_CHUNK_SAMPLES),
                           _SILERO_CHUNK_SAMPLES):
            w = audio[start:start + _SILERO_CHUNK_SAMPLES]
            if len(w) < _SILERO_CHUNK_SAMPLES:
                w = np.pad(w, (0, _SILERO_CHUNK_SAMPLES - len(w)))
            if self._probability(w) >= self._threshold:
                self._speaking = True
                self._quiet_windows = 0
            elif self._speaking:
                self._quiet_windows += 1
                if self._quiet_windows >= self._silence_windows_needed:
                    self._speaking = False
        return self._speaking

    def reset(self) -> None:
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(_CONTEXT_SAMPLES, dtype=np.float32)
        self._speaking = False
        self._quiet_windows = 0

    @classmethod
    def is_available(cls) -> bool:
        """onnxruntime present, and the weights cached or fetchable.

        Reports available when onnxruntime imports; construction handles the
        one-time download and raises if it cannot, so get_vad() falls back.
        """
        try:
            import onnxruntime  # noqa: F401, PLC0415
            return True
        except ImportError:
            return False
