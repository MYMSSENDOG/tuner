"""Validation of the offline reference annotator against synthesized truth.

The annotator is the source of truth for real-audio tests, so its own
accuracy bar is far stricter than the app's (sub-cent on stable pitch).
"""

import numpy as np
import pytest

from tests.helpers import cents_error
from tests.synth import SR, add_noise, glissando, glissando_freqs, tone
from tuner.analysis.reference import annotate
from tuner.core.notes import note_to_freq

MAX_STABLE_ERROR_CENTS = 0.5


def labeled(windows):
    return [w for w in windows if w.freq_hz is not None]


@pytest.mark.parametrize("instrument", ["pure", "violin", "cello", "flute"])
def test_stable_tone_subcent(instrument):
    worst = 0.0
    for name, octave in [("G", 3), ("A", 4), ("D", 6), ("E", 6)]:
        freq = note_to_freq(name, octave)
        windows = labeled(annotate(tone(freq, 0.5, instrument=instrument), SR))
        assert len(windows) >= 5
        for w in windows:
            worst = max(worst, abs(cents_error(w.freq_hz, freq)))
    print(f"\nannotator {instrument}: worst stable error {worst:.4f} cents")
    assert worst <= MAX_STABLE_ERROR_CENTS


def test_noisy_tone_still_tight():
    freq = note_to_freq("A", 4)
    signal = add_noise(tone(freq, 0.5, instrument="violin"), 10.0, seed=3)
    windows = labeled(annotate(signal, SR))
    assert len(windows) >= 5
    for w in windows:
        assert abs(cents_error(w.freq_hz, freq)) <= 2.0


def test_glissando_window_centers():
    duration = 2.0
    signal = glissando(400.0, 800.0, duration, instrument="violin")
    truth = glissando_freqs(400.0, 800.0, duration)
    windows = labeled(annotate(signal, SR))
    assert len(windows) >= 20
    for w in windows:
        center = int((w.t0 + w.t1) / 2 * SR)
        assert abs(cents_error(w.freq_hz, truth[center])) <= 5.0


def test_faint_room_tone_terminates_and_unlabeled():
    """Near-silent noise once drove the parabolic interpolation to nonsense
    (even negative) frequencies, hanging the comb-coverage loop forever."""
    from tuner.core.spectral import estimate_f0

    rng = np.random.default_rng(7)
    frame = rng.standard_normal(8192) * 1e-4
    _freq, conf = estimate_f0(frame, SR)
    assert conf < 0.9


def test_silence_and_noise_unlabeled():
    assert labeled(annotate(np.zeros(SR), SR)) == []
    rng = np.random.default_rng(1)
    windows = annotate(rng.standard_normal(SR), SR)
    assert len(labeled(windows)) <= len(windows) * 0.05
