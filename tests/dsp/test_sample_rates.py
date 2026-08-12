"""The pipeline at sample rates real devices actually use.

Everything else in the suite runs at 44.1kHz, but the engine feeds the
detector whatever rate the device opened at — 48kHz is the common case on
modern interfaces. Frame constants are sample counts, so every derived
quantity (window seconds, tau bounds, fmin reach) shifts with the rate;
this pins accuracy and the advertised range at those rates.
"""

import math

import numpy as np
import pytest

from tests.synth import tone
from tuner.core.detector import YinDetector
from tuner.core.notes import note_to_freq
from tuner.core.tracker import PitchTracker

RATES = (44100, 48000)
NOTES = [("E", 2), ("A", 3), ("A", 4), ("E", 6)]  # low reach to violin top
MAX_ERROR_CENTS = 2.0


def tracked_median(signal: np.ndarray, sr: int) -> float | None:
    detector, tracker = YinDetector(), PitchTracker()
    readings = []
    for start in range(0, len(signal) - detector.frame_size + 1, detector.hop_size):
        result = tracker.update(detector.detect(signal[start : start + detector.frame_size], sr))
        if result.freq_hz:
            readings.append(result.freq_hz)
    return float(np.median(readings)) if len(readings) >= 10 else None


@pytest.mark.parametrize("sr", RATES)
@pytest.mark.parametrize("name,octave", NOTES, ids=[f"{n}{o}" for n, o in NOTES])
def test_accuracy_holds_at_device_rates(sr, name, octave):
    freq = note_to_freq(name, octave)
    signal = tone(freq, 0.4, instrument="violin", sr=sr)
    detected = tracked_median(signal, sr)
    assert detected is not None, f"{name}{octave} unreadable at {sr}Hz"
    error = 1200.0 * math.log2(detected / freq)
    assert abs(error) <= MAX_ERROR_CENTS, f"{name}{octave} @ {sr}Hz: {error:+.2f}c"


@pytest.mark.parametrize("sr", RATES)
def test_low_register_reach_holds(sr):
    """The advertised floor (double bass E1, 41Hz via the long window) must
    not silently shrink when the device runs at 48kHz — the long window
    holds fewer periods of a given pitch at higher rates."""
    freq = 41.2
    signal = tone(freq, 0.6, instrument="cello", sr=sr)
    detected = tracked_median(signal, sr)
    assert detected is not None, f"E1 unreadable at {sr}Hz"
    assert abs(1200 * math.log2(detected / freq)) <= 10.0
