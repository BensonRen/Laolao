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
            else:
                log.info("silero-vad not installed; falling back to energy VAD. "
                         "Run: pip install silero-vad")
        except Exception as e:
            log.debug("Silero VAD failed to load: %s", e)

    from vad.energy_vad import EnergyVAD
    log.info("VAD: Energy (lightweight, no extra deps)")
    return EnergyVAD(cfg)
