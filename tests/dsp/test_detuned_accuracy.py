"""Cent accuracy on real instrument sound at arbitrary detunings.

The fixture corpus is played in tune, so it never checks the tuner's core
scenario: a note that is 10, 30 or 49 cents off. Reinterpreting a real
clip's sample rate as sr * 2^(c/1200) shifts its pitch by exactly c cents —
no resynthesis, no artifacts, and the ground truth is arithmetic on the
clip's annotated pitch. Real timbre, exact detuned truth.

The +-49 cases also pin behavior at the semitone boundary, where the note
classification must flip sides consistently with the cents sign.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tuner.core.detector import YinDetector
from tuner.core.tracker import PitchTracker

BANK = Path(__file__).parent.parent / "fixtures" / "notes"

# spread of registers and timbres, including the weak-fundamental oboe
CLIPS = [
    ("violin", "A4"),
    ("violin", "G3"),
    ("cello", "C2"),
    ("flute", "C6"),
    ("trumpet", "G3"),
    ("oboe", "F5"),
]
DETUNE_CENTS = (-49.0, -30.0, -10.0, +10.0, +30.0, +49.0)
MAX_ERROR_CENTS = 3.0

requires_bank = pytest.mark.skipif(
    not (BANK / "bank.json").exists(), reason="note bank not built"
)


def annotated_hz(instrument: str, note: str) -> float:
    manifest = json.loads((BANK / "bank.json").read_text())
    return manifest[instrument][note]["freq_hz"]


def tracked_median_hz(signal: np.ndarray, sr: float) -> float | None:
    detector, tracker = YinDetector(), PitchTracker()
    readings = []
    for start in range(0, len(signal) - detector.frame_size + 1, detector.hop_size):
        result = tracker.update(detector.detect(signal[start : start + detector.frame_size], sr))
        if result.freq_hz:
            readings.append(result.freq_hz)
    return float(np.median(readings)) if len(readings) >= 10 else None


@requires_bank
@pytest.mark.parametrize("instrument,note", CLIPS, ids=[f"{i}-{n}" for i, n in CLIPS])
def test_detuned_pitch_read_exactly(instrument, note):
    signal, sr = sf.read(BANK / instrument / f"{note}.flac")
    base_hz = annotated_hz(instrument, note)
    worst = 0.0
    for cents in DETUNE_CENTS:
        rate = 2.0 ** (cents / 1200.0)
        truth = base_hz * rate
        detected = tracked_median_hz(signal, sr * rate)
        assert detected is not None, f"{instrument}/{note} at {cents:+.0f}c: no reading"
        error = 1200.0 * math.log2(detected / truth)
        worst = max(worst, abs(error))
        assert abs(error) <= MAX_ERROR_CENTS, (
            f"{instrument}/{note} detuned {cents:+.0f}c: read {error:+.2f}c off the truth"
        )
    print(f"\n{instrument}/{note}: worst error over detunings {worst:.2f}c")
