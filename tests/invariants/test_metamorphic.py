"""Wall 2 — transformations that must not change what the meter shows.

Every other display test needs to know the right answer. These do not: they
run the same audio twice, transformed in a way the physics of pitch does not
care about, and demand the same reading out of both. That makes them immune
to retuning — a threshold sweep, a smoothing change, a different detector
default all move both runs identically — while catching the class of mistake
an unattended refactor actually makes: a buffer that depends on how the audio
happened to be chopped up, a normalisation that snuck into the wrong place, a
reference pitch that leaks into detection.

Nothing here is a measured value. The tolerances are floating-point slack, not
quality bars: the invariants below are exact in arithmetic, and the numbers
only exist because doubles are not.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.invariants.pipeline import BLOCK, displayed_hz, readings
from tests.synth import sequence, tone
from tuner.core.notes import note_to_freq
from tuner.core.pitch import DEFAULT_HOP_SIZE
from tuner.tools.trace import CENTS_TOL  # a difference the meter could draw

# Floating point, not quality: the meter's own resolution is 0.5 cents
# (tools/trace.py CENTS_TOL) and these differences are three orders below it.
FP_SLACK_CENTS = 0.01

A4 = note_to_freq("A", 4)


def cents(f1: float, f2: float) -> float:
    return 1200.0 * math.log2(f1 / f2)


def violin_note() -> np.ndarray:
    return tone(A4, 0.8, instrument="violin")


def short_phrase() -> np.ndarray:
    """Attacks, a leap and gaps — the parts of the pipeline that carry state
    across frames (floor tracking, jump confirmation, the latch) all wake up."""
    return sequence(
        [A4, note_to_freq("C", 5), note_to_freq("A", 3)],
        note_duration=0.35,
        instrument="violin",
    )


def assert_same_display(a, b, label: str, tol: float = FP_SLACK_CENTS) -> None:
    """Same readings, in the same order, showing the same pitch."""
    left, right = displayed_hz(a), displayed_hz(b)
    assert len(left) == len(right), f"{label}: {len(left)} readings vs {len(right)}"
    for i, (x, y) in enumerate(zip(left, right, strict=True)):
        assert (x is None) == (y is None), f"{label}: reading {i} is {x} vs {y}"
        if x is not None and y is not None:
            assert abs(cents(x, y)) <= tol, f"{label}: reading {i} {x:.4f}Hz vs {y:.4f}Hz"


# ------------------------------------------------------- the same sound, louder


@pytest.mark.parametrize("gain", [0.25, 3.0])
def test_loudness_does_not_change_the_pitch(gain):
    """YIN normalises its difference function by its own running mean, so the
    answer is scale-free by construction. A normalisation that ever stopped
    being one — an absolute threshold on d(tau), a magnitude compared against
    a constant — would show up here and nowhere else in the suite, because
    every fixture is played at one level.

    Both runs stay well clear of the input gate, which is an absolute level
    and legitimately not scale-free.
    """
    signal = violin_note()
    assert_same_display(readings(signal), readings(signal * gain), f"gain {gain}")


def test_polarity_does_not_change_the_pitch():
    """A microphone wired the other way round hears the same note. YIN squares
    differences, so this is exact arithmetic, not an approximation."""
    signal = short_phrase()
    assert_same_display(readings(signal), readings(-signal), "inverted")


def test_a_dc_offset_does_not_change_the_pitch():
    """Real interfaces carry one. It cancels inside the difference function,
    and the spectral cross-check must not let bin zero in either."""
    signal = violin_note()
    assert_same_display(readings(signal), readings(signal + 0.02), "dc offset")


# --------------------------------------------------- the same sound, rechopped


@pytest.mark.parametrize("block_size", [64, 128, BLOCK])
def test_the_block_size_does_not_change_the_display(block_size):
    """The device decides how the stream is cut up; the engine's ring buffer
    exists so that decision cannot reach the display. Detections happen at
    fixed sample offsets, so as long as the blocks divide the hop the readings
    must be identical — not merely similar.

    (Blocks larger than the hop are excluded because they genuinely change the
    schedule: the engine detects at most once per block, so a 1024-sample
    block produces a quarter of the readings. That is the device's rate, not
    a display policy.)
    """
    signal = short_phrase()
    assert DEFAULT_HOP_SIZE % block_size == 0
    assert_same_display(
        readings(signal, block_size=BLOCK),
        readings(signal, block_size=block_size),
        f"block {block_size}",
        tol=0.0,  # same samples, same order: bit-for-bit
    )


def test_silence_in_front_does_not_change_what_follows():
    """Nothing a player sees may depend on how long the room was quiet first.

    Not bit-for-bit, and it should not be: the padded run's frames straddle
    the moment the note starts, so it has seen a few windows of half-silence
    that the unpadded run never had, and the smoother carries that difference
    forward. The wall is that it stays invisible — the meter shows 100 cents
    across ~200 pixels (tools/trace.py CENTS_TOL), and this is a fortieth of
    what it can draw. The frame-by-frame state (reading or no reading) must
    match exactly all the same.
    === driver: measured worst 0.043c at the first reading, p95 0.030c.
    """
    pad_hops = 86  # ~0.5s, and an exact number of detections
    signal = short_phrase()
    padded = np.concatenate([np.zeros(pad_hops * DEFAULT_HOP_SIZE), signal])

    plain = displayed_hz(readings(signal))
    delayed = displayed_hz(readings(padded))[pad_hops:]
    assert len(plain) == len(delayed)
    for i, (x, y) in enumerate(zip(plain, delayed, strict=True)):
        assert (x is None) == (y is None), f"reading {i}: {x} vs {y}"
        if x is not None and y is not None:
            assert abs(cents(x, y)) <= CENTS_TOL, f"reading {i}: {x:.4f} vs {y:.4f}"


# ------------------------------------------------------ the same sound, retuned


def test_the_reference_pitch_moves_the_number_and_nothing_else():
    """A4 is a naming convention, not a measurement. Changing it must leave
    every detected frequency untouched and shift every displayed deviation by
    exactly the interval between the two references — the one place a stray
    a4_hz inside the detector or the tracker would betray itself.
    """
    signal = short_phrase()
    at_440 = readings(signal, a4_hz=440.0)
    at_442 = readings(signal, a4_hz=442.0)
    assert_same_display(at_440, at_442, "a4 442 vs 440", tol=0.0)

    shift = -cents(442.0, 440.0)  # a higher reference reads the same pitch flatter
    compared = 0
    for left, right in zip(at_440, at_442, strict=True):
        if left.note is None or right.note is None or left.note.label != right.note.label:
            continue  # a boundary the two references fall on either side of
        if max(abs(left.note.cents), abs(right.note.cents)) > 49.0:
            continue  # a held name's number saturates (core/notes.py ceiling)
        assert right.note.cents - left.note.cents == pytest.approx(shift, abs=0.01)
        compared += 1
    assert compared > 50, f"only {compared} readings compared"


def test_the_transformations_are_not_all_no_ops():
    """Power check: an assertion over an empty or silent run passes happily.
    The signals above must actually produce readings, and the sanity of the
    whole file rests on them being the pitch that was played."""
    played = readings(violin_note())
    shown = [hz for hz in displayed_hz(played) if hz is not None]
    assert len(shown) > 100
    assert abs(cents(float(np.median(shown)), A4)) <= 2.0
    # and a transformation that *should* change the display does
    other = readings(tone(note_to_freq("C", 5), 0.8, instrument="violin"))
    with pytest.raises(AssertionError):
        assert_same_display(played, other, "different note")


# Deliberately not here: sample-rate invariance. It is measured against ground
# truth at real device rates (44.1k/48k) in tests/dsp/test_sample_rates.py, and
# a same-signal-twice version of it here would only be a weaker copy.
