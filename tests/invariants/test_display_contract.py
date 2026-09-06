"""Wall 3 — the three things on screen must describe the same pitch.

The meter shows a name, a number and (through the needle) a frequency. They
are produced by different code — the tracker smooths the frequency, the latch
chooses the name, the latch also decides what number a held name may admit to
— and nothing until now stated that they have to agree with each other.

That statement is the wall: given the frequency and the name, the number is
arithmetic, with exactly one documented exception (a held name saturates at
the meter's edge). No threshold, no fixture and no tuning changes it, so these
tests can only fail because the display started lying.

Nothing here is a measured value. NOTE_HOLD_CENTS_CEILING is a product rule
(docs/note-latch-tuning.md), not a knob to widen when a case fails.
"""

from __future__ import annotations

import inspect
import math
import re
from pathlib import Path

import numpy as np
import pytest

from tests.invariants.pipeline import readings
from tests.synth import SR, sequence, tone
from tuner.core.notes import NOTE_HOLD_CENTS_CEILING, note_to_freq
from tuner.core.pitch import DEFAULT_FRAME_SIZE
from tuner.core.tracker import PitchTracker, State
from tuner.tools.trace import trace_file

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"

# Chosen for what they make the latch do, not for their pitch: a scale changes
# the name repeatedly, a vibrato straddles a boundary, the interference file is
# where octave glitches (and therefore holds) actually happen, and the cello C2
# is the low-register path.
FIXTURES = [
    "violin_scale_G3B3.aiff",
    "flute_vib_C6.aif",
    "trumpet_novib_G3.bg-flute_vib_C6.snr15.wav",
    "cello_arco_C2.aif",
]

# The trace writes hz to 4 decimals and cents to 3, so a reconstruction from
# those numbers cannot land closer than this.
ROUNDING_SLACK_CENTS = 0.02
# In process the same arithmetic runs twice over the same double, so all that
# separates the two answers is the log2 round trip.
FP_SLACK_CENTS = 1e-9

# YIN's search range (core/pitch.py fmin/fmax, plus the low-register window's
# own floor). A displayed pitch outside it did not come from the detector.
DISPLAY_RANGE_HZ = (38.0, 3000.0)

# Whatever the tracker refuses to look at cannot bound what it displays.
MIN_CONFIDENCE = inspect.signature(PitchTracker).parameters["min_confidence"].default


def split_label(label: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-G]#?)(-?\d+)", label)
    assert match, f"unparseable note label: {label!r}"
    return match[1], int(match[2])


def cents_from_name(label: str, hz: float, a4_hz: float = 440.0) -> float:
    """How far the shown frequency actually is from the shown name."""
    name, octave = split_label(label)
    return 1200.0 * math.log2(hz / note_to_freq(name, octave, a4_hz))


def expected_cents(label: str, hz: float, a4_hz: float = 440.0) -> float:
    """What the badge must read: the true deviation, saturated at the edge of
    the scale while a name is being held (core/notes.py NOTE_HOLD_CENTS_CEILING
    — "the number never leaves +-50")."""
    raw = cents_from_name(label, hz, a4_hz)
    return math.copysign(min(abs(raw), NOTE_HOLD_CENTS_CEILING), raw)


def check_triple(label: str, cents: float, hz: float, slack: float, where: str) -> None:
    assert abs(cents) <= NOTE_HOLD_CENTS_CEILING + slack, (
        f"{where}: {label} {cents:+.2f}c is off the meter's scale"
    )
    want = expected_cents(label, hz)
    assert abs(cents - want) <= slack, (
        f"{where}: shows {label} {cents:+.3f}c at {hz:.4f}Hz — that pitch is "
        f"{cents_from_name(label, hz):+.3f}c from {label}, so the badge should "
        f"read {want:+.3f}c"
    )


def phrase() -> np.ndarray:
    return sequence(
        [note_to_freq("A", 4), note_to_freq("C", 5), note_to_freq("A", 3)],
        note_duration=0.35,
        instrument="violin",
    )


# ------------------------------------------------- the name, number and pitch


def test_the_name_and_the_number_agree_on_synthesis():
    shown = [r.note for r in readings(phrase()) if r.note is not None]
    assert len(shown) > 100
    for i, note in enumerate(shown):
        check_triple(note.label, note.cents, note.freq_hz, FP_SLACK_CENTS, f"reading {i}")


@pytest.mark.parametrize("filename", FIXTURES)
def test_the_name_and_the_number_agree_on_recordings(filename):
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    frames = [f for f in trace_file(path).frames if f.label is not None]
    assert frames, "no readings produced"
    for frame in frames:
        check_triple(
            frame.label, frame.cents, frame.hz, ROUNDING_SLACK_CENTS, f"{filename} frame {frame.i}"
        )


def test_a_disagreeing_triple_is_caught():
    """Power check: the arithmetic above must actually reject the failure it
    exists for — a name left over from before a leap, with the new pitch's
    deviation printed next to it."""
    stale = "A4"
    moved_to = note_to_freq("C", 5)
    with pytest.raises(AssertionError):
        check_triple(stale, +12.0, moved_to, ROUNDING_SLACK_CENTS, "fabricated")
    # and the ceiling is a ceiling
    with pytest.raises(AssertionError):
        check_triple(stale, +2400.0, moved_to, ROUNDING_SLACK_CENTS, "fabricated")


# ------------------------------------------------------- state and value pair


@pytest.mark.parametrize(
    "name,signal",
    [
        ("silence", np.zeros(SR // 2)),
        ("noise", np.random.default_rng(4).normal(0.0, 0.05, SR // 2)),
        ("tone", tone(note_to_freq("A", 4), 0.5, instrument="violin")),
        ("phrase", phrase()),
    ],
)
def test_a_reading_has_a_note_exactly_when_it_says_it_does(name, signal):
    """TunerReading documents "note: present iff state is OK". Everything
    downstream — meter, trace, field report — branches on that."""
    for i, reading in enumerate(readings(signal)):
        assert (reading.note is not None) == (reading.state is State.OK), (
            f"{name} reading {i}: state {reading.state.value} with note {reading.note}"
        )


# ------------------------------------------- the display invents no frequency


@pytest.mark.parametrize("filename", FIXTURES)
def test_the_display_never_leaves_the_detector_behind(filename):
    """Smoothing is a filter, not a source. Every displayed frequency must lie
    between the smallest and largest the detector has confidently reported so
    far — a filter that overshoots, or a unit slip that scales the output,
    puts a number on screen that no frame of audio ever supported.

    The band is the whole history rather than a window on purpose: it must
    hold across holds, dropouts and confirmed jumps, and a wall that needs a
    window length is a wall with a tuning knob.
    """
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    low = high = None
    checked = 0
    for frame in trace_file(path).frames:
        if frame.raw_hz is not None and frame.confidence >= MIN_CONFIDENCE:
            low = frame.raw_hz if low is None else min(low, frame.raw_hz)
            high = frame.raw_hz if high is None else max(high, frame.raw_hz)
        if frame.hz is None:
            continue
        assert low is not None, f"{filename} frame {frame.i}: a reading before any detection"
        # 0.001 cents of slack for the rounding in the trace file
        assert low * 0.9999994 <= frame.hz <= high * 1.0000006, (
            f"{filename} frame {frame.i}: displays {frame.hz:.3f}Hz, outside the "
            f"detected {low:.3f}-{high:.3f}Hz"
        )
        checked += 1
    assert checked > 100, f"{filename}: only {checked} readings"


@pytest.mark.parametrize("filename", FIXTURES)
def test_the_displayed_pitch_stays_in_the_detector_range(filename):
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    shown = [f.hz for f in trace_file(path).frames if f.hz is not None]
    assert shown
    assert DISPLAY_RANGE_HZ[0] <= min(shown) and max(shown) <= DISPLAY_RANGE_HZ[1]


def test_the_band_check_would_catch_an_overshoot():
    """Power check: the band is only a wall if a value outside it fails."""
    low, high = 440.0, 441.0
    assert not (low * 0.9999994 <= 442.0 <= high * 1.0000006)
    assert low * 0.9999994 <= 440.5 <= high * 1.0000006
    # and the frame the tracker reads is the one the trace records
    assert DEFAULT_FRAME_SIZE == 2048
