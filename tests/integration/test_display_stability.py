"""Display steadiness on real recordings — the user-visible failure mode.

A tuner that reports the right pitch but repaints a different note name
several times per second is unusable. These tests bound how much the
*displayed* note is allowed to move on recordings whose note content is
known, which is what the meter's stabilization machinery (input gate,
tracker jump confirmation, note latch) exists to guarantee.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
import soundfile as sf

from tests.fakes import FakeAudioInput
from tuner.app.engine import TunerEngine, TunerReading
from tuner.core.notes import Note

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"


def displayed_notes(path: Path) -> list[Note | None]:
    """Notes the app would show, in order, for a whole recording."""
    signal, sr = sf.read(path, always_2d=True)
    fake = FakeAudioInput(signal.mean(axis=1), sr=sr)
    readings: list[TunerReading] = []
    engine = TunerEngine(fake, readings.append)
    engine.start()
    fake.pump()
    engine.stop()
    return [r.note for r in readings]


def displayed_labels(path: Path) -> list[str | None]:
    """Note labels the app would show, in order, for a whole recording."""
    return [n.label if n is not None else None for n in displayed_notes(path)]


def label_segments(labels: list[str | None]) -> list[str]:
    """Consecutive runs of the same displayed name, silence removed."""
    return [label for label, _ in itertools.groupby(l for l in labels if l is not None)]


# (file, notes actually played, allowed extra segments)
# The allowance covers attack transients, where an instrument genuinely
# sounds another pitch briefly (flute register transitions) — but it is
# small enough that flicker regressions fail loudly.
CASES = [
    ("violin_scale_G3B3.aiff", 5, 1),
    ("violin_arco_A4.aif", 1, 1),
    ("violin_arco_G3.snr20.wav", 1, 1),
    # 14: this note vibrates across a semitone boundary (C6 +30..+70c), and
    # the rule that the number never leaves +-50 means the name follows the
    # pitch to whichever side it is on. Was 2 while the name was held past
    # the boundary instead - a motionless "+50" for up to 75ms per cycle.
    # === driver: docs/note-latch-tuning.md "+-50 rule" - product decision.
    ("flute_vib_C6.aif", 1, 14),
    ("trumpet_vib_A4.aif", 1, 1),
    ("trumpet_novib_G3.bg-flute_vib_C6.snr15.wav", 1, 2),
    ("cello_arco_A3.aif", 1, 1),
    ("oboe_scale_C4B4.aiff", 12, 4),
]


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
    notes = [n for n in displayed_notes(path) if n is not None]
    assert notes, "no readings produced"
    worst = max(notes, key=lambda n: abs(n.cents))
    print(f"\n{filename}: worst {worst.label} {worst.cents:+.0f}c of {len(notes)} readings")
    assert abs(worst.cents) <= METER_RANGE, (
        f"displayed {worst.label} {worst.cents:+.0f}c - off the meter's scale"
    )


# Readings whose number is stuck at the ceiling: the needle is pinned there
# too, so nothing on screen moves. A pitch parked just past the boundary is
# exactly what tuning an out-of-tune instrument looks like, so this cannot be
# allowed to last — it read as the meter having stopped working.
FROZEN_FRAMES_ALLOWED = 40  # ~230ms; worst measured is 16 frames / 93ms


def longest_frozen_run(notes: list[Note | None]) -> int:
    from tuner.core.notes import NOTE_HOLD_CENTS_CEILING

    longest = run = 0
    for note in notes:
        if note is not None and abs(note.cents) >= NOTE_HOLD_CENTS_CEILING - 0.5:
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
    frozen = longest_frozen_run(displayed_notes(path))
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

    now = longest_frozen_run(displayed_notes(path))
    monkeypatch.setattr(engine_mod, "NoteLatch", _NoRelease)
    held = longest_frozen_run(displayed_notes(path))
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

    bounded = max(abs(n.cents) for n in displayed_notes(path) if n is not None)
    monkeypatch.setattr(engine_mod, "NoteLatch", _NoCeiling)
    unbounded = max(abs(n.cents) for n in displayed_notes(path) if n is not None)
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
