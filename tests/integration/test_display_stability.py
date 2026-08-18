"""Display steadiness on real recordings — the user-visible failure mode.

A tuner that reports the right pitch but repaints a different note name
several times per second is unusable. These tests bound how much the
*displayed* note is allowed to move on recordings whose note content is
known, which is what the meter's stabilization machinery (input gate,
tracker jump confirmation, note latch) exists to guarantee.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tuner.tools.trace import (
    BRIEF_FRAMES,
    TraceFrame,
    brief_flashes,
    label_segments,
    trace_file,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"


# The tracer runs the same engine over the same blocks these tests used to
# assemble by hand, and it defines the display metrics in one place so that
# the dev tool (python -m tuner.tools.trace) and this suite cannot drift
# apart — a metric measured twice is a metric nobody can trust.


def displayed_frames(path: Path) -> list[TraceFrame]:
    """Every frame the app would display, in order, for a whole recording."""
    return trace_file(path).frames


def displayed_labels(path: Path) -> list[str | None]:
    """Note labels the app would show, in order, for a whole recording."""
    return trace_file(path).labels


# (file, notes actually played, allowed extra segments)
#
# A segment count is a runaway cap, not a fine seal. Under the +-50 rule a
# boundary-straddling vibrato legitimately alternates names, so on some
# fixtures this number is identical with the latch on and off — it can still
# catch a display that repaints wildly, but it cannot tell whether the latch
# works. That job belongs to test_no_name_flashes_briefly below, which counts
# only the runs too short to read.
#
# Allowances therefore sit a little above the measured value rather than
# exactly on it: three of these used to pass with zero slack, so any unrelated
# change (a detector tweak, a numpy version) broke them for no reason.
# === driver: measured latch-on segments are 5 / 1 / 1 / 15 / 2 / 2 / 2 / 13.
CASES = [
    ("violin_scale_G3B3.aiff", 5, 1),
    ("violin_arco_A4.aif", 1, 1),
    ("violin_arco_G3.snr20.wav", 1, 1),
    # 17: this note vibrates across a semitone boundary (C6 +30..+70c), and
    # the rule that the number never leaves +-50 means the name follows the
    # pitch to whichever side it is on (measured 15, and 15 with the latch off
    # too). Was 2 while the name was held past the boundary instead - a
    # motionless "+50" for up to 75ms per cycle.
    # === driver: docs/note-latch-tuning.md "+-50 rule" - product decision.
    ("flute_vib_C6.aif", 1, 17),
    ("trumpet_vib_A4.aif", 1, 2),
    ("trumpet_novib_G3.bg-flute_vib_C6.snr15.wav", 1, 2),
    ("cello_arco_A3.aif", 1, 2),
    ("oboe_scale_C4B4.aiff", 12, 4),
]

# BRIEF_FRAMES (8 frames = 46ms) lives in tuner/tools/trace.py with its
# rationale: a name painted for fewer frames than that is not readable as a
# note, it is a flash, and genuine alternation is far slower.
# Measured latch-on counts are 0 everywhere except one flash each on
# flute_vib_C6 and trumpet_vib_A4 (both attack transients), so 2 leaves every
# fixture a frame of slack. With the latch off the interference fixture jumps
# to 6 — see test_brief_flash_bound_is_actually_doing_something.
MAX_BRIEF_FLASHES = 2


@pytest.mark.parametrize(
    "filename", [c[0] for c in CASES], ids=[c[0].split(".")[0] for c in CASES]
)
def test_no_name_flashes_briefly(filename):
    """The seal a segment count cannot provide: names that appear too briefly
    to read. Alternation the +-50 rule requires is slow enough to pass; the
    octave glitches the latch exists to absorb are not.
    """
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    flashes = brief_flashes(displayed_labels(path))
    print(f"\n{filename}: {flashes} runs shorter than {BRIEF_FRAMES} frames")
    assert flashes <= MAX_BRIEF_FLASHES


def test_brief_flash_bound_is_actually_doing_something(monkeypatch):
    """Power check: with the latch off, the interference fixture flashes names
    it cannot read. This is where the latch's measured value actually sits —
    on most fixtures latch on and off score the same.
    """
    from tuner.core import notes

    path = FIXTURE_DIR / "trumpet_novib_G3.bg-flute_vib_C6.snr15.wav"
    if not path.exists():
        pytest.skip("fixture not present")

    with_latch = brief_flashes(displayed_labels(path))
    monkeypatch.setattr(notes, "NOTE_LATCH_ENABLED", False)
    without_latch = brief_flashes(displayed_labels(path))
    print(f"\ninterference brief flashes: latch on {with_latch}, off {without_latch}")
    assert with_latch <= MAX_BRIEF_FLASHES < without_latch


@pytest.mark.parametrize(
    "filename,played,allowance", CASES, ids=[c[0].split(".")[0] for c in CASES]
)
def test_display_does_not_flicker(filename, played, allowance):
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    segments = label_segments(displayed_labels(path))
    print(f"\n{filename}: {len(segments)} segments (played {played}) {segments[:12]}")
    assert len(segments) <= played + allowance, f"display flicker: {segments}"


# Recordings whose note content changes, i.e. where the display has to cross
# semitone boundaries for real. Scales exercise leaps the single-note
# fixtures never reach.
CENTS_RANGE_CASES = [
    "violin_scale_G3B3.aiff",
    "oboe_scale_C4B4.aiff",
    "flute_scale_B3B4.aiff",
    "cello_scale_A2Gb3.aiff",
    "flute_vib_C6.aif",
    "trumpet_vib_A4.aif",
]

# A semitone of "deviation" is a different note, not a deviation. The meter
# reads -50..+50; the latch may sit past that while it holds a boundary
# wobble, but never by a whole semitone.
MAX_DISPLAYED_CENTS = 100.0

# The real bound: the meter reads -50..+50 and the number never leaves it.
# Past the boundary the pitch belongs to the neighbouring note and is shown
# there instead. MAX_DISPLAYED_CENTS above only survives for the power check,
# which needs headroom above this to tell 'clamped' from 'not clamped'.
METER_RANGE = 50.0


@pytest.mark.parametrize(
    "filename", CENTS_RANGE_CASES, ids=[c.split(".")[0] for c in CENTS_RANGE_CASES]
)
def test_displayed_cents_stay_in_range(filename):
    """No reading may claim the instrument is a semitone or more off.

    A note change used to be shown as its full interval on the previous
    name (a two-octave leap read +2400c) until the latch caught up.
    """
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    shown = [f for f in displayed_frames(path) if f.cents is not None]
    assert shown, "no readings produced"
    worst = max(shown, key=lambda f: abs(f.cents))
    print(f"\n{filename}: worst {worst.label} {worst.cents:+.0f}c of {len(shown)} readings")
    assert abs(worst.cents) <= METER_RANGE, (
        f"displayed {worst.label} {worst.cents:+.0f}c - off the meter's scale"
    )


# Readings whose number is stuck at the ceiling: the needle is pinned there
# too, so nothing on screen moves. A pitch parked just past the boundary is
# exactly what tuning an out-of-tune instrument looks like, so this cannot be
# allowed to last — it read as the meter having stopped working.
FROZEN_FRAMES_ALLOWED = 40  # ~230ms; worst measured is 16 frames / 93ms


def longest_frozen_run(frames: list[TraceFrame]) -> int:
    from tuner.core.notes import NOTE_HOLD_CENTS_CEILING

    longest = run = 0
    for frame in frames:
        if frame.cents is not None and abs(frame.cents) >= NOTE_HOLD_CENTS_CEILING - 0.5:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


@pytest.mark.parametrize(
    "filename", CENTS_RANGE_CASES, ids=[c.split(".")[0] for c in CENTS_RANGE_CASES]
)
def test_display_does_not_freeze(filename):
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    frozen = longest_frozen_run(displayed_frames(path))
    print()
    print(f"{filename}: longest frozen run {frozen} frames")
    assert frozen <= FROZEN_FRAMES_ALLOWED


def test_freeze_bound_is_actually_doing_something(monkeypatch):
    """Power check: with the release window shut, the same recording freezes
    for far longer. The pitch there genuinely wanders +52..+68, so holding the
    old name is what pins the number at the end of the scale.
    """
    from tuner.app import engine as engine_mod
    from tuner.core import notes as notes_mod

    path = FIXTURE_DIR / "cello_scale_A2Gb3.aiff"
    if not path.exists():
        pytest.skip("fixture not present")

    class _NoRelease(notes_mod.NoteLatch):
        """Only the release window is disabled - the dwell keeps
        behaving normally, or this would measure a differently-broken latch."""

        def __init__(self, *args, **kwargs):
            kwargs["release_low_cents"] = float("inf")
            super().__init__(*args, **kwargs)

    now = longest_frozen_run(displayed_frames(path))
    monkeypatch.setattr(engine_mod, "NoteLatch", _NoRelease)
    held = longest_frozen_run(displayed_frames(path))
    print()
    print(f"cello scale longest frozen run: now {now}, without release {held}")
    assert held > FROZEN_FRAMES_ALLOWED >= now


def test_cents_ceiling_is_actually_doing_something(monkeypatch):
    """Power check: without the hold ceiling the same recording must report a
    wild deviation — proving the test above can fail and the ceiling is what
    prevents it."""
    from tuner.app import engine as engine_mod
    from tuner.core import notes as notes_mod

    path = FIXTURE_DIR / "oboe_scale_C4B4.aiff"
    if not path.exists():
        pytest.skip("fixture not present")

    class _NoCeiling(notes_mod.NoteLatch):
        """Only the clamp is disabled — the dwell must keep behaving normally,
        or this would measure a differently-broken latch instead."""

        def __init__(self, *args, **kwargs):
            kwargs["ceiling_cents"] = float("inf")
            super().__init__(*args, **kwargs)

    bounded = max(abs(f.cents) for f in displayed_frames(path) if f.cents is not None)
    monkeypatch.setattr(engine_mod, "NoteLatch", _NoCeiling)
    unbounded = max(abs(f.cents) for f in displayed_frames(path) if f.cents is not None)
    print(f"\noboe scale worst cents: ceiling on {bounded:.0f}c, off {unbounded:.0f}c")
    assert bounded < MAX_DISPLAYED_CENTS <= unbounded


def test_stabilization_is_actually_doing_something(monkeypatch):
    """Power check: with the note latch off, a recording where the detector
    jumps an octave under interference must flicker, proving the test above
    can fail and the latch is what prevents it.

    This used to point at the vibrato flute. It no longer can: under the +-50
    rule the name follows a boundary-straddling vibrato by design, so latch
    and no-latch score the same there. What the latch still buys is exactly
    this - suppressing octave glitches under interference.
    """
    from tuner.core import notes

    path = FIXTURE_DIR / "trumpet_novib_G3.bg-flute_vib_C6.snr15.wav"
    if not path.exists():
        pytest.skip("fixture not present")

    with_latch = len(label_segments(displayed_labels(path)))
    monkeypatch.setattr(notes, "NOTE_LATCH_ENABLED", False)
    without_latch = len(label_segments(displayed_labels(path)))
    print(f"\nflute segments: latch on {with_latch}, off {without_latch}")
    assert without_latch >= with_latch + 5
