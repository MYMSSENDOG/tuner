"""Selectable pitch-detector implementations behind one interface.

The engine consumes any PitchDetector; the UI lets the user pick one.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from tuner.core import pitch
from tuner.core.pitch import PitchResult
from tuner.core.spectral import estimate_f0, restore_weak_fundamental


class PitchDetector(Protocol):
    name: str
    frame_size: int  # samples of context each detection needs
    hop_size: int  # samples between detections; must exceed one detection's compute time

    def detect(self, frame: np.ndarray, sr: int) -> PitchResult: ...


class YinDetector:
    """Default: fastest response (small frame), proven noise robustness."""

    name = "YIN (fast)"
    frame_size = pitch.DEFAULT_FRAME_SIZE
    hop_size = pitch.DEFAULT_HOP_SIZE

    def detect(self, frame: np.ndarray, sr: int) -> PitchResult:
        result = pitch.detect(frame, sr)
        if result.freq_hz is None:
            return result
        # lag-domain YIN cannot tell T from T/k when the fundamental is a
        # few percent of the energy (oboe, low brass); one spectral
        # cross-check restores it
        freq = restore_weak_fundamental(frame, sr, result.freq_hz)
        if freq == result.freq_hz:
            return result
        return PitchResult(freq_hz=freq, confidence=result.confidence)


class SpectralDetector:
    """The reference annotator's estimator at real-time settings.

    More precise on stable pitch, but needs a 4096-sample trailing window
    (~93ms at 44.1kHz), so it reacts more slowly than YIN.
    """

    name = "Spectral (precise)"
    frame_size = 4096
    hop_size = 1024  # heavier per detection; ~23ms budget at 44.1kHz

    def detect(self, frame: np.ndarray, sr: int) -> PitchResult:
        freq, confidence = estimate_f0(frame, sr, dtft_rounds=3)
        return PitchResult(freq_hz=freq, confidence=confidence)


DETECTORS: tuple[type[PitchDetector], ...] = (YinDetector, SpectralDetector)
