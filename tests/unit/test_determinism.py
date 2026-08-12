"""The synthesis layer must be bit-for-bit deterministic.

Every cent-level assertion in the suite leans on synthesized ground truth;
if any of it picked up hidden randomness (an unseeded RNG, time-dependent
state), tests would flake and — worse — fixture regeneration would silently
change the corpus. Hashes pin the exact output.
"""

import numpy as np

from tests.sequence_bank import BANK_DIR, build_sequence, major_scale
from tests.synth import add_noise, glissando, sequence, tone


def digest(x: np.ndarray) -> str:
    import hashlib

    return hashlib.sha256(np.ascontiguousarray(x).tobytes()).hexdigest()[:16]


def test_synthesis_is_deterministic():
    for make in (
        lambda: tone(440.0, 0.3, instrument="violin", vibrato_cents=15.0),
        lambda: add_noise(tone(440.0, 0.3), 10.0, seed=3),
        lambda: glissando(200.0, 400.0, 0.5, instrument="cello"),
        lambda: sequence([440.0, 550.0], 0.2),
    ):
        assert digest(make()) == digest(make())


def test_noise_seeds_are_independent():
    base = tone(440.0, 0.2)
    assert digest(add_noise(base, 10.0, seed=1)) != digest(add_noise(base, 10.0, seed=2))


def test_stitched_sequences_are_deterministic():
    import pytest

    if not (BANK_DIR / "bank.json").exists():
        pytest.skip("note bank not built")
    a, _sr, _ref = build_sequence("violin", major_scale("G4"))
    b, _sr, _ref = build_sequence("violin", major_scale("G4"))
    assert digest(a) == digest(b)
