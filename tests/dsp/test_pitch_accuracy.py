"""Accuracy: clean synthesized scales per instrument, cent-level assertions."""

import numpy as np
import pytest

from tests.helpers import cents_error, detect_median_hz
from tests.synth import tone
from tuner.core.notes import note_to_freq

MAX_ERROR_CENTS = 2.0

# realistic-ish playable ranges as (name, octave) endpoints, inclusive, chromatic
INSTRUMENT_RANGES = {
    "violin": (("G", 3), ("E", 7)),
    "cello": (("C", 2), ("A", 5)),
    "flute": (("C", 4), ("C", 7)),
    "guitar": (("E", 2), ("E", 5)),
    "voice": (("C", 3), ("C", 6)),
    "pure": (("G", 3), ("E", 7)),
}


def chromatic_freqs(low: tuple[str, int], high: tuple[str, int], a4_hz: float) -> list[float]:
    f_low = note_to_freq(*low, a4_hz=a4_hz)
    f_high = note_to_freq(*high, a4_hz=a4_hz)
    n_semitones = round(12 * np.log2(f_high / f_low))
    return [f_low * 2 ** (k / 12) for k in range(n_semitones + 1)]


@pytest.mark.parametrize("instrument", sorted(INSTRUMENT_RANGES))
@pytest.mark.parametrize("a4_hz", [440.0, 442.0])
def test_chromatic_scale_accuracy(instrument, a4_hz):
    low, high = INSTRUMENT_RANGES[instrument]
    worst = 0.0
    for freq in chromatic_freqs(low, high, a4_hz):
        signal = tone(freq, 0.15, instrument=instrument)
        detected = detect_median_hz(signal)
        error = cents_error(detected, freq)
        worst = max(worst, abs(error))
        assert abs(error) <= MAX_ERROR_CENTS, (
            f"{instrument} @ {freq:.2f}Hz (A4={a4_hz}): error {error:+.2f} cents"
        )
    print(f"\n{instrument} A4={a4_hz}: worst error {worst:.3f} cents")


def test_vibrato_tracks_center():
    # violin vibrato ±20 cents: median over a full period should sit near center
    freq = note_to_freq("A", 4)
    signal = tone(freq, 1.0, instrument="violin", vibrato_cents=20.0)
    detected = detect_median_hz(signal)
    assert abs(cents_error(detected, freq)) <= 5.0
