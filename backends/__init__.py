"""
Laolao transcription backends.

Auto-selects the best backend for the current platform:
  - macOS Apple Silicon  →  MLXBackend            (mlx-whisper, uses Neural Engine)
  - NVIDIA GPU available →  FasterWhisperBackend(device="cuda")
  - Windows on ARM64     →  OnnxWhisperBackend    (onnxruntime; QNN/Hexagon if available)
  - Fallback             →  FasterWhisperBackend(device="cpu", int8)

Windows-on-ARM64 note: faster-whisper cannot run there at all — its ctranslate2
dependency publishes no win-arm64 distribution — so the ONNX backend is tried
*before* the CPU fallback on that platform. See docs/snapdragon/NORTH_STAR.md.
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

    # ── ONNX Runtime / Qualcomm Hexagon NPU ──────────────────────────────
    # Only pre-empts the CPU fallback where faster-whisper genuinely cannot
    # run, so x86-64 and macOS keep their existing behaviour untouched.
    # On Windows ARM64 this is not a preference, it is the only option — no
    # other backend can physically run — so the branch is taken unconditionally
    # rather than only for device="auto".
    if _is_windows_arm64() or forced_device in ("onnx", "qnn", "npu"):
        try:
            from backends.onnx_whisper_backend import get_onnx_whisper_backend
            return get_onnx_whisper_backend(_arm64_cfg(cfg))
        except Exception as e:
            log.debug("ONNX backend failed to load: %s", e)
            if forced_device in ("onnx", "qnn", "npu") or _is_windows_arm64():
                raise

    # ── CPU fallback ─────────────────────────────────────────────────────
    from backends.faster_whisper_backend import FasterWhisperBackend
    cpu_cfg = {**cfg, "device": "cpu", "compute_type": cfg.get("compute_type", "int8")}
    log.info("Backend: faster-whisper (CPU int8)")
    try:
        return FasterWhisperBackend(cpu_cfg)
    except ImportError as e:
        raise RuntimeError(_no_backend_message(e)) from e


# Whisper sizes with a precompiled Qualcomm QNN export. Anything else has no
# NPU asset and silently drops to the (much slower) CPU execution provider.
QNN_MODELS = frozenset({"tiny", "base", "large-v3-turbo"})
QNN_DEFAULT_MODEL = "base"


# Device values the ONNX backend understands. Anything else (notably "mlx",
# which config.json ships) is meaningless here.
ONNX_DEVICES = frozenset({"auto", "cpu", "qnn", "npu"})


def _arm64_cfg(cfg: dict) -> dict:
    """Normalise a config written for Apple Silicon / x86 into one that works here.

    Two shipped defaults are actively harmful on this platform, and both fail
    *quietly* — which for a live-captioning tool means the user simply thinks
    the app is broken rather than misconfigured:

    - `device: "mlx"` is Apple-only. It is not a value the ONNX backend
      recognises, so it silently selected the CPU provider instead of the
      Hexagon NPU — roughly 25x slower, and nothing in the logs says why.
    - `model: "small"` has no precompiled Qualcomm export, so it also drops to
      CPU. Measured, that lands ~12 s behind the conversation.

    Substituting loudly beats failing quietly. `qnn_strict: false` opts out.
    """
    out = dict(cfg)
    strict = cfg.get("qnn_strict", True)

    device = str(cfg.get("device", "auto")).lower()
    if device not in ONNX_DEVICES:
        log.warning(
            "device=%r is not usable on Windows ARM64 (it is an Apple/CUDA value); "
            "using 'auto' so the Hexagon NPU can be selected.", cfg.get("device"),
        )
        out["device"] = "auto"

    model = cfg.get("model", QNN_DEFAULT_MODEL)
    if model not in QNN_MODELS and strict:
        log.warning(
            "Model %r has no Qualcomm NPU export — it would fall back to CPU and run "
            "roughly 25x slower than the NPU path. Using %r instead. "
            "Supported here: %s. Set \"qnn_strict\": false in config.json to override.",
            model, QNN_DEFAULT_MODEL, ", ".join(sorted(QNN_MODELS)),
        )
        out["model"] = QNN_DEFAULT_MODEL

    return out


def _no_backend_message(err: Exception) -> str:
    """Explain the dead end instead of leaking a bare ctranslate2 ImportError."""
    if _is_windows_arm64():
        return (
            f"No transcription backend is available on Windows ARM64 ({err}).\n"
            "faster-whisper cannot work here: its ctranslate2 dependency ships no "
            "win-arm64 distribution.\n"
            "Install the ONNX backend instead:  pip install onnxruntime\n"
            "See docs/snapdragon/NORTH_STAR.md for the full platform picture."
        )
    return (
        f"No transcription backend is available ({err}).\n"
        "Install one with:  pip install faster-whisper"
    )


def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _is_windows_arm64() -> bool:
    return platform.system() == "Windows" and platform.machine().lower() in (
        "arm64", "aarch64"
    )


def _has_cuda() -> bool:
    try:
        import ctranslate2
        return "cuda" in ctranslate2.get_supported_compute_types("cuda")
    except Exception:
        return False
