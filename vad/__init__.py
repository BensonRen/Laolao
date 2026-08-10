"""
Laolao VAD (Voice Activity Detection) backends.

Auto-selects the best available VAD:
  - silero   →  SileroVAD  (accurate, requires silero-vad package)
  - energy   →  EnergyVAD  (lightweight, no extra deps, always available)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vad.base import BaseVAD

log = logging.getLogger("laolao.vad")


def get_vad(cfg: dict) -> "BaseVAD":
    """Return the configured VAD, falling back to energy-based if unavailable."""

    vad_type = cfg.get("vad", "auto")

    if vad_type in ("auto", "silero"):
        try:
            from vad.silero_vad import SileroVAD
            if SileroVAD.is_available():
                log.info("VAD: Silero (accurate neural VAD)")
                return SileroVAD(cfg)
        except Exception as e:
            log.debug("Silero VAD (package) failed to load: %s", e)

        # The silero-vad package declares a hard torch dependency, so it cannot
        # be installed where torch has no wheel (Windows ARM64). The weights
        # themselves are plain ONNX, so run them directly instead of dropping
        # all the way to energy — the accuracy difference is the difference
        # between captioning speech and captioning room noise.
        try:
            from vad.silero_vad import SileroOnnxVAD
            if SileroOnnxVAD.is_available():
                log.info("VAD: Silero via onnxruntime (no torch required)")
                return SileroOnnxVAD(cfg)
            log.info("onnxruntime not installed; falling back to energy VAD.")
        except Exception as e:
            log.info("Silero ONNX VAD unavailable (%s); falling back to energy VAD.", e)

    from vad.energy_vad import EnergyVAD
    log.info("VAD: Energy (lightweight, no extra deps)")
    return EnergyVAD(cfg)
