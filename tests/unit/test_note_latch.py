"""Note-name latch: hysteresis + dwell, and its on/off switch."""

import pytest

from tuner.core import notes
from tuner.core.notes import NoteLatch, note_to_freq

A4 = note_to_freq("A", 4)


def cents_off(base_hz: float, cents: float) -> float:
    return base_hz * 2 ** (cents / 1200)


def test_plain_tracking_of_nearest_note():
    latch = NoteLatch()
    assert latch.update(A4).label == "A4"
    assert latch.update(cents_off(A4, 30)).label == "A4"


def test_holds_name_just_past_the_boundary():
    """+55c is past halfway but inside hysteresis: still A4.

    The reported deviation saturates at the meter's range rather than reading
    +55 — while a name is held the true distance to it is not something the
    meter can show, and the needle is already pinned there.
    (Driver: docs/note-latch-tuning.md; 65c was the measured alternative.)
    """
    latch = NoteLatch()
    latch.update(A4)
    held = latch.update(cents_off(A4, 55))
    assert held.label == "A4"
    assert held.cents == pytest.approx(notes.NOTE_HOLD_CENTS_CEILING)


def test_vibrato_peak_does_not_relabel():
    """Excursions beyond the boundary shorter than dwell keep the name."""
    latch = NoteLatch(dwell_frames=12)
    latch.update(A4)
    for _ in range(8):  # 8 frames out, then back — a vibrato peak
        assert latch.update(cents_off(A4, 70)).label == "A4"
    assert latch.update(A4).label == "A4"


def test_sustained_move_relabels():
    latch = NoteLatch(dwell_frames=12)
    latch.update(A4)
    labels = [latch.update(cents_off(A4, 70)).label for _ in range(20)]
    assert labels[-1] == "A#4"
    assert labels.count("A4") == 11  # holds until the dwell-th frame, then switches


# A deviation of a whole semitone is not a deviation — it is a different
# note. Whatever the latch does while holding, it must never report that the
# instrument is a semitone or more off the name on screen.
SEMITONE_CENTS = 100.0


@pytest.mark.parametrize(
    "semitones,expected",
    [(1, "A#4"), (2, "B4"), (7, "E5"), (12, "A5"), (24, "A6")],
    ids=["semitone", "whole-tone", "fifth", "octave", "two-octaves"],
)
def test_leap_is_not_reported_as_deviation_from_the_old_note(semitones, expected):
    """A real note change must not be shown as a huge offset on the old name.

    The latch exists to absorb jitter around a semitone boundary; a leap is
    not jitter. Holding through one makes the meter read e.g. A4 +2400c.
    """
    latch = NoteLatch()
    latch.update(A4)
    target = cents_off(A4, semitones * 100.0)

    reported = [latch.update(target) for _ in range(20)]
    worst = max(abs(n.cents) for n in reported)
    assert worst < SEMITONE_CENTS, (
        f"reported {worst:+.0f}c while holding "
        f"{reported[0].label!r} through a {semitones}-semitone leap"
    )
    assert reported[-1].label == expected


def test_hold_reports_off_the_scale_not_the_raw_distance():
    """The name may lag a leap by the dwell — that is the latch's documented
    cost — but the deviation it reports while lagging must stay bounded.

    Releasing the hold early instead was measured and rejected: it broke the
    flicker sealing on interference fixtures (docs/note-latch-tuning.md).
    """
    latch = NoteLatch()
    latch.update(A4)
    held = [latch.update(cents_off(A4, 2400)) for _ in range(11)]

    assert [n.label for n in held] == ["A4"] * 11  # dwell still holds the name
    assert all(n.cents == pytest.approx(notes.NOTE_HOLD_CENTS_CEILING) for n in held)
    assert latch.update(cents_off(A4, 2400)).label == "A6"  # then it catches up


def test_hold_keeps_the_sign_of_the_deviation():
    """Clamping must not lose which way the pitch went."""
    latch = NoteLatch()
    latch.update(A4)
    assert latch.update(cents_off(A4, -2400)).cents == pytest.approx(
        -notes.NOTE_HOLD_CENTS_CEILING
    )


def test_reset_clears_hold():
    latch = NoteLatch()
    latch.update(A4)
    latch.reset()
    assert latch.update(cents_off(A4, 55)).label == "A#4"  # no hold to keep


def test_switch_disables_latch(monkeypatch):
    monkeypatch.setattr(notes, "NOTE_LATCH_ENABLED", False)
    latch = NoteLatch()
    latch.update(A4)
    assert latch.update(cents_off(A4, 55)).label == "A#4"  # raw nearest note
