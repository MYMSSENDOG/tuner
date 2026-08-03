"""Shared measurement helpers for DSP tests."""

from __future__ import annotations

import math

import numpy as np

from tuner.core.pitch import DEFAULT_FRAME_SIZE, DEFAULT_HOP_SIZE, detect

from tests.synth import SR


def cents_error(detected_hz: float, true_hz: float) -> float:
    return 1200.0 * math.log2(detected_hz / true_hz)


def detect_frames(
    signal: np.ndarray,
    sr: int = SR,
    frame_size: int = DEFAULT_FRAME_SIZE,
    hop: int = DEFAULT_HOP_SIZE,
    min_confidence: float = 0.5,
) -> list[float]:
    """Per-frame detected frequencies, confidence-gated."""
    freqs = []
    for start in range(0, len(signal) - frame_size + 1, hop):
        result = detect(signal[start : start + frame_size], sr)
        if result.freq_hz is not None and result.confidence >= min_confidence:
            freqs.append(result.freq_hz)
    return freqs


def detect_median_hz(signal: np.ndarray, sr: int = SR, **kwargs) -> float:
    freqs = detect_frames(signal, sr, **kwargs)
    assert freqs, "no confident pitch detected in signal"
    return float(np.median(freqs))


def track_signal(
    signal: np.ndarray,
    sr: int = SR,
    detector=None,
) -> list[tuple[float, float | None]]:
    """Feed signal through the real-time pipeline exactly as the engine does.

    Returns (frame_end_time_seconds, displayed_freq) per hop — frame end is
    the moment this reading could exist in real time.
    """
    from tuner.core.detector import YinDetector
    from tuner.core.tracker import PitchTracker

    detector = detector or YinDetector()
    tracker = PitchTracker()
    frame_size = detector.frame_size
    out = []
    for start in range(0, len(signal) - frame_size + 1, detector.hop_size):
        tracked = tracker.update(detector.detect(signal[start : start + frame_size], sr))
        out.append(((start + frame_size) / sr, tracked.freq_hz))
    return out
