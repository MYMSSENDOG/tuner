"""Stitched real-note sequences: scales, arpeggios, chromatic runs, melodies.

Each sequence is assembled at test time from the committed note bank
(tests/fixtures/notes/), with ground truth carried over from the bank's
per-clip pitch timelines — no annotation cost, vibrato-aware, and easy to
multiply. This is the primary data set for tuning tracker/smoothing
parameters: lots of realistic note transitions per instrument.
"""

import numpy as np
import pytest

from tests.helpers import (
    LOW_REGISTER_HZ,
    LOW_REGISTER_TOLERANCE_CENTS,
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
    ode_to_joy,
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
}

CASES = [
    pytest.param(instrument, pattern, id=f"{instrument}-{pattern}")
    for instrument in INSTRUMENTS
    for pattern in PATTERNS
]


@pytest.mark.parametrize("instrument,pattern", CASES)
def test_sequence(instrument, pattern):
    root, chromatic_range = INSTRUMENTS[instrument]
    melody = PATTERNS[pattern](root, chromatic_range)
    signal, sr, ref = build_sequence(instrument, melody)

    errors = compare_app_to_reference(signal, sr, 0.05, ref)
    labeled = [w.freq_hz for w in ref if w.freq_hz is not None]
    low_register = float(np.median(labeled)) < LOW_REGISTER_HZ
    assert_pipeline_agreement(
        errors,
        f"{instrument}-{pattern}",
        clean_tolerance=LOW_REGISTER_TOLERANCE_CENTS if low_register else TOLERANCE_CENTS,
    )


def test_bank_covers_declared_ranges():
    for instrument, (_root, (lo, hi)) in INSTRUMENTS.items():
        notes = bank_notes(instrument)
        assert lo in notes and hi in notes, f"{instrument}: bank missing range endpoints"
