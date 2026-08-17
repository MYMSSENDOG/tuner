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
    ("flute_vib_C6.aif", 1, 2),
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
    assert abs(worst.cents) < MAX_DISPLAYED_CENTS, (
        f"displayed {worst.label} {worst.cents:+.0f}c"
    )


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
    """Power check: with the note latch off, a vibrato note that straddles a
    semitone boundary must flicker — proving the test above can fail and the
    latch is what prevents it."""
    from tuner.core import notes

    path = FIXTURE_DIR / "flute_vib_C6.aif"
    if not path.exists():
        pytest.skip("fixture not present")

    with_latch = len(label_segments(displayed_labels(path)))
    monkeypatch.setattr(notes, "NOTE_LATCH_ENABLED", False)
    without_latch = len(label_segments(displayed_labels(path)))
    print(f"\nflute segments: latch on {with_latch}, off {without_latch}")
    assert without_latch >= with_latch + 5
