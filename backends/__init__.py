"""
Laolao transcription backends.

Auto-selects the best backend for the current platform:
  - macOS Apple Silicon  →  MLXBackend   (mlx-whisper, uses Neural Engine)
  - NVIDIA GPU available →  FasterWhisperBackend(device="cuda")
  - Fallback             →  FasterWhisperBackend(device="cpu", int8)
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backends.base import BaseBackend

log = logging.getLogger("laolao.backends")


def get_backend(cfg: dict) -> "BaseBackend":
    """Return the best available transcription backend for this system."""

    forced_device = cfg.get("device", "auto")

    # ── Apple Silicon → MLX ──────────────────────────────────────────────
    if forced_device in ("auto", "mlx") and _is_apple_silicon():
        try:
            from backends.mlx_backend import MLXBackend
            if MLXBackend.is_available():
                log.info("Backend: MLX (Apple Silicon Neural Engine)")
                return MLXBackend(cfg)
            else:
                log.info("MLX not installed. Run: pip install mlx-whisper")
        except Exception as e:
            log.debug("MLX backend failed to load: %s", e)

    # ── CUDA GPU ─────────────────────────────────────────────────────────
    if forced_device in ("auto", "cuda") and _has_cuda():
        try:
            from backends.faster_whisper_backend import FasterWhisperBackend
            cuda_cfg = {**cfg, "device": "cuda", "compute_type": "float16"}
            log.info("Backend: faster-whisper (CUDA)")
            return FasterWhisperBackend(cuda_cfg)
        except Exception as e:
            log.debug("CUDA backend failed: %s", e)

    # ── CPU fallback ─────────────────────────────────────────────────────
    from backends.faster_whisper_backend import FasterWhisperBackend
    cpu_cfg = {**cfg, "device": "cpu", "compute_type": cfg.get("compute_type", "int8")}
    log.info("Backend: faster-whisper (CPU int8)")
    return FasterWhisperBackend(cpu_cfg)


def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _has_cuda() -> bool:
    try:
        import ctranslate2
        return "cuda" in ctranslate2.get_supported_compute_types("cuda")
    except Exception:
        return False
