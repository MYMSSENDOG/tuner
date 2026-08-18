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


# The product rule: the number never leaves +-50. At +51c the instrument is
# not "51 sharp of A4", it is 49 flat of A#4 — and that is what the meter says.
# Everything else here (hysteresis, dwell) may only act where it does not
# contradict this. (Driver: docs/note-latch-tuning.md.)
METER_RANGE = 50.0


def test_crossing_the_boundary_moves_to_the_neighbour():
    latch = NoteLatch()
    latch.update(A4)
    assert latch.update(cents_off(A4, 48)).label == "A4"

    crossed = latch.update(cents_off(A4, 52))
    assert crossed.label == "A#4"
    assert crossed.cents == pytest.approx(-48.0, abs=0.5)


def test_the_number_never_leaves_the_scale():
    """Sweep a glissando across two boundaries; every reading must be
    displayable on the meter, and the name must be the one it is measured
    from.

    Before this rule the same sweep sat at the end of the scale waiting out
    the dwell — measured at up to 1521ms motionless on cello_scale_A2Gb3.
    """
    latch = NoteLatch()
    latch.update(A4)
    for cents in range(0, 260, 4):
        note = latch.update(cents_off(A4, cents))
        assert abs(note.cents) <= METER_RANGE, f"{note.label} {note.cents:+.0f}c"
        # the name is genuinely the nearest one, not a leftover
        assert note.freq_hz == pytest.approx(
            note_to_freq(note.name, note.octave) * 2 ** (note.cents / 1200)
        )


def test_vibrato_across_a_boundary_flips_the_name():
    """The accepted cost of the rule above, stated outright.

    A vibrato straddling a semitone boundary now repaints the name on every
    cycle — the exact thing the dwell was built to stop (flute_vib_C6: 3 -> 15
    segments). The rule wins anyway: a motionless number reads as a broken
    meter, and the pitch really is on both sides of the boundary.
    """
    latch = NoteLatch()
    latch.update(A4)
    labels = [latch.update(cents_off(A4, c)).label for c in (40, 60, 40, 60)]
    assert labels == ["A4", "A#4", "A4", "A#4"]


# A move to a neighbouring note is not the boundary wobble the dwell exists to
# absorb. Waiting it out only spends the dwell showing the old name pinned at
# the end of the scale — the "why does it stop at +-50 on the way" report.
@pytest.mark.parametrize(
    "cents,expected",
    [(100, "A#4"), (-100, "G#4"), (200, "B4"), (-200, "G4")],
    ids=["semitone-up", "semitone-down", "tone-up", "tone-down"],
)
def test_neighbouring_note_relabels_without_waiting(cents, expected):
    latch = NoteLatch()
    latch.update(A4)
    moved = latch.update(cents_off(A4, cents))
    assert moved.label == expected
    assert abs(moved.cents) < 5.0  # reads as the new note, not off the old one


def test_release_window_has_an_upper_bound():
    """Power check for the bound: an octave-scale departure must still wait
    out the dwell. Under interference the detector jumps that far on its own
    (+-560..2521c measured), and relabelling those on sight is what broke the
    flicker seal — the window is what separates the two cases.
    """
    latch = NoteLatch()
    latch.update(A4)
    held = [latch.update(cents_off(A4, 1200)) for _ in range(11)]
    assert [n.label for n in held] == ["A4"] * 11


def test_glitch_excursion_shorter_than_dwell_keeps_the_name():
    """Excursions past the *release window* (i.e. octave-scale, a detection
    glitch) shorter than the dwell still keep the name — that guard is intact,
    it just no longer applies to distances the meter can express."""
    latch = NoteLatch(dwell_frames=12)
    latch.update(A4)
    for _ in range(8):
        assert latch.update(cents_off(A4, 1200)).label == "A4"
    assert latch.update(A4).label == "A4"


def test_sustained_glitch_eventually_relabels():
    latch = NoteLatch(dwell_frames=12)
    latch.update(A4)
    labels = [latch.update(cents_off(A4, 1200)).label for _ in range(20)]
    assert labels[-1] == "A5"
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
