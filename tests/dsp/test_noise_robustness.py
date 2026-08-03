"""Noise robustness: same scales under degraded SNR.

Criteria (PLAN 4.2): at SNR 10dB error <= ±5 cents with zero octave errors.
SNR 5dB is reported but held to a looser bound.
"""

import numpy as np
import pytest

from tuner.core.notes import note_to_freq

from tests.helpers import cents_error, detect_frames, detect_median_hz
from tests.synth import add_noise, tone

# (snr_db, max_cents_error)
SNR_CRITERIA = [(20.0, 3.0), (10.0, 5.0), (5.0, 8.0)]

VIOLIN_G_MAJOR = [
    ("G", 3), ("A", 3), ("B", 3), ("C", 4), ("D", 4), ("E", 4), ("F#", 4), ("G", 4),
    ("A", 4), ("B", 4), ("C", 5), ("D", 5), ("E", 5), ("F#", 5), ("G", 5),
    ("A", 5), ("B", 5), ("C", 6), ("D", 6), ("E", 6), ("F#", 6), ("G", 6),
]


@pytest.mark.parametrize("snr_db,max_cents", SNR_CRITERIA)
def test_violin_scale_under_noise(snr_db, max_cents):
    worst = 0.0
    octave_errors = 0
    for seed, (name, octave) in enumerate(VIOLIN_G_MAJOR):
        freq = note_to_freq(name, octave)
        signal = add_noise(tone(freq, 0.2, instrument="violin"), snr_db, seed=seed)
        detected = detect_median_hz(signal)
        error = cents_error(detected, freq)
        if abs(error) > 300:  # semitone-scale miss = octave/harmonic error
            octave_errors += 1
            continue
        worst = max(worst, abs(error))
        assert abs(error) <= max_cents, (
            f"{name}{octave} @ SNR {snr_db}dB: error {error:+.2f} cents"
        )
    assert octave_errors == 0, f"{octave_errors} octave errors at SNR {snr_db}dB"
    print(f"\nSNR {snr_db}dB: worst error {worst:.3f} cents, 0 octave errors")


def test_pure_noise_rejected():
    """Noise with no tonal content must not produce confident detections."""
    rng = np.random.default_rng(42)
    noise = rng.standard_normal(44100)
    confident = detect_frames(noise, min_confidence=0.7)
    assert len(confident) == 0


def test_silence_rejected():
    silence = np.zeros(44100)
    assert detect_frames(silence, min_confidence=0.0) == []
