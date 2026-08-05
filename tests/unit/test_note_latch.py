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
    """+55c is past halfway but inside hysteresis: still A4, reported as +55."""
    latch = NoteLatch()
    latch.update(A4)
    held = latch.update(cents_off(A4, 55))
    assert held.label == "A4"
    assert held.cents == pytest.approx(55, abs=0.1)


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
