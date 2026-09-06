"""Wall 4 — frequencies whose answer is arithmetic, off the semitone grid.

tests/dsp/test_pitch_accuracy.py sweeps chromatic scales: every truth in it is
a note, and every period is one of a few dozen values. The detector's known
traps live between those values — a period of 99.77 samples is compared
against dips at integer lags, and the octave decision is decided by which dip
looks deeper (docs/pitch-pipeline.md 2-3). A bug that only bites at some
awkward ratio can pass the grid forever.

So this sweeps frequencies drawn at random from each timbre's playable range.
The truth is the number we synthesised, the corpus is generated rather than
stored, and the criterion (+-2 cents) is this project's stated accuracy bar,
not a threshold anyone may tune. Seeds are fixed: a failure is reproducible
and belongs in tests/fixtures/, not in a rerun.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.metrics import record
from tests.synth import SR, tone
from tuner.core.detector import YinDetector

# docs/pitch-pipeline.md "검출 가능 범위": the real-time path reaches ~41Hz
# (double bass E1) at the bottom and 3000Hz at the top. Each timbre is swept
# only where it is played — a cello profile at 2.5kHz measures the
# synthesizer's harmonic stack, not the detector.
SWEEP_RANGES_HZ = {
    "pure": (41.0, 2637.0),  # the whole documented reach; E7 is the violin's top
    "violin": (196.0, 2637.0),
    "cello": (41.0, 880.0),  # stands in for the double bass at the bottom
    "flute": (262.0, 2093.0),
    "guitar": (82.0, 659.0),
    "voice": (131.0, 1047.0),
}
FREQS_PER_TIMBRE = 24
SEED = 20260906

# The project's accuracy criterion (tests/dsp/test_pitch_accuracy.py). Measured
# worst over this sweep is 0.04c — an octave error would read 1200.
MAX_ERROR_CENTS = 2.0

TONE_SECONDS = 0.16  # ~9 detections per frequency after the frame fills


def detected_hz(freq_hz: float, instrument: str) -> float | None:
    """Median of the detector's confident readings on a steady tone.

    The detector, not the tracker: this wall is about the frequency the
    instrument produced, and a steady tone gives the display policy nothing
    to do (its own invariants are tests/invariants/test_display_contract.py).
    """
    detector = YinDetector()
    signal = tone(freq_hz, TONE_SECONDS, instrument=instrument)
    confident = []
    for start in range(0, len(signal) - detector.frame_size + 1, detector.hop_size):
        result = detector.detect(signal[start : start + detector.frame_size], SR)
        if result.freq_hz is not None and result.confidence >= 0.5:
            confident.append(result.freq_hz)
    return float(np.median(confident)) if confident else None


def cents(detected: float, truth: float) -> float:
    return 1200.0 * math.log2(detected / truth)


def sweep_frequencies(instrument: str) -> list[float]:
    """Log-uniform over the range: even coverage in the domain pitch lives in,
    and no reason for a drawn value to sit near a semitone."""
    low, high = SWEEP_RANGES_HZ[instrument]
    rng = np.random.default_rng(SEED + sorted(SWEEP_RANGES_HZ).index(instrument))
    return [
        float(f)
        for f in np.exp(rng.uniform(math.log(low), math.log(high), FREQS_PER_TIMBRE))
    ]


@pytest.mark.parametrize("instrument", sorted(SWEEP_RANGES_HZ))
def test_frequencies_off_the_semitone_grid(instrument):
    worst = 0.0
    for freq in sweep_frequencies(instrument):
        detected = detected_hz(freq, instrument)
        assert detected is not None, f"{instrument} @ {freq:.2f}Hz: no confident reading"
        error = cents(detected, freq)
        worst = max(worst, abs(error))
        assert abs(error) <= MAX_ERROR_CENTS, (
            f"{instrument} @ {freq:.3f}Hz: read {detected:.3f}Hz ({error:+.1f} cents)"
        )
    record(f"invariants/offgrid_{instrument}/worst_cents", worst)


# Two octaves apart in the low register, where the multiples of a true period
# are all inside the analysis window and the wrong dip is cheapest to pick.
OCTAVE_BASES_HZ = [46.0, 61.7, 82.4, 110.0, 146.8, 196.0, 261.6, 329.6, 440.0, 587.3]


@pytest.mark.parametrize("instrument", ["pure", "violin"])
def test_the_octave_is_exactly_a_factor_of_two(instrument):
    """Doubling a frequency doubles the reading — no more, no less.

    Accuracy alone would catch an octave error, but not what it is: this names
    the mechanism the detector spends three defences on (interpolated dips,
    smallest-period preference, multiple verification), so a failure here says
    "octave" before anyone opens a trace.
    """
    low, high = SWEEP_RANGES_HZ[instrument]
    worst = 0.0
    checked = 0
    for base in OCTAVE_BASES_HZ:
        if base < low or base * 2 > high:
            continue
        below, above = detected_hz(base, instrument), detected_hz(base * 2, instrument)
        assert below is not None and above is not None, f"{instrument} @ {base}Hz"
        deviation = cents(above / below, 2.0)
        worst = max(worst, abs(deviation))
        assert abs(deviation) <= MAX_ERROR_CENTS, (
            f"{instrument}: {base:.1f}Hz read {below:.2f}, {base * 2:.1f}Hz read "
            f"{above:.2f} — ratio is {deviation:+.1f} cents from an octave"
        )
        checked += 1
    assert checked >= 5
    record(f"invariants/octave_{instrument}/worst_cents", worst)


def test_the_sweep_would_notice_a_wrong_answer():
    """Power check: a sweep that measured nothing would pass silently, and a
    criterion that admitted an octave would pass the bug it exists for."""
    assert len(sweep_frequencies("violin")) == FREQS_PER_TIMBRE
    # frequencies are drawn off the grid: none of them is a semitone of A440
    for freq in sweep_frequencies("violin"):
        semitones = 12 * math.log2(freq / 440.0)
        assert abs(semitones - round(semitones)) > 0.01

    truth = 440.0
    detected = detected_hz(truth, "violin")
    assert detected is not None
    assert abs(cents(detected, truth)) <= MAX_ERROR_CENTS
    assert abs(cents(detected, truth * 2)) > MAX_ERROR_CENTS  # an octave miss is 1200c
