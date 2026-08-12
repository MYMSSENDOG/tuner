"""Stitched real-note sequences: scales, arpeggios, chromatic runs, melodies.

Each sequence is assembled at test time from the committed note bank
(tests/fixtures/notes/), with ground truth carried over from the bank's
per-clip pitch timelines — no annotation cost, vibrato-aware, and easy to
multiply. This is the primary data set for tuning tracker/smoothing
parameters: lots of realistic note transitions per instrument.
"""

import pytest

from tests.helpers import (
    TOLERANCE_CENTS,
    assert_pipeline_agreement,
    compare_app_to_reference,
)
from tests.sequence_bank import (
    BANK_DIR,
    arpeggio,
    bank_notes,
    build_sequence,
    chromatic_scale,
    major_scale,
    octave_leaps,
    ode_to_joy,
    tchaikovsky4_oboe,
    twinkle,
)

pytestmark = pytest.mark.skipif(
    not (BANK_DIR / "bank.json").exists(),
    reason="note bank not built (tuner.tools.build_note_bank)",
)

# (instrument, scale/arpeggio root, full chromatic range)
INSTRUMENTS = {
    "violin": ("G4", ("G3", "A5")),
    "cello": ("C2", ("C2", "A3")),
    "flute": ("C5", ("C4", "C6")),
    "trumpet": ("C4", ("G3", "C5")),
}

PATTERNS = {
    "major_scale": lambda root, rng: major_scale(root),
    "arpeggio": lambda root, rng: arpeggio(root),
    "chromatic": lambda root, rng: chromatic_scale(*rng),
    "twinkle": lambda root, rng: twinkle(root),
    "ode_to_joy": lambda root, rng: ode_to_joy(root),
    "octave_leaps": lambda root, rng: octave_leaps(root),
}

CASES = [
    pytest.param(instrument, pattern, id=f"{instrument}-{pattern}")
    for instrument in INSTRUMENTS
    for pattern in PATTERNS
]


def run_sequence_case(instrument: str, melody, label: str) -> None:
    signal, sr, ref = build_sequence(instrument, melody)
    errors = compare_app_to_reference(signal, sr, 0.05, ref)
    assert_pipeline_agreement(errors, label, clean_tolerance=TOLERANCE_CENTS)


@pytest.mark.parametrize("instrument,pattern", CASES)
def test_sequence(instrument, pattern):
    root, chromatic_range = INSTRUMENTS[instrument]
    melody = PATTERNS[pattern](root, chromatic_range)
    run_sequence_case(instrument, melody, f"{instrument}-{pattern}")


EXCERPTS = [
    pytest.param("oboe", tchaikovsky4_oboe, id="oboe-tchaikovsky4-andantino"),
]


@pytest.mark.parametrize("instrument,melody_fn", EXCERPTS)
def test_famous_excerpt(instrument, melody_fn):
    run_sequence_case(instrument, melody_fn(), f"{instrument}-{melody_fn.__name__}")


def test_bank_covers_declared_ranges():
    for instrument, (_root, (lo, hi)) in INSTRUMENTS.items():
        notes = bank_notes(instrument)
        assert lo in notes and hi in notes, f"{instrument}: bank missing range endpoints"
