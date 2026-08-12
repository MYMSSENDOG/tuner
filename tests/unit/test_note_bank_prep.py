"""Bank clip preparation invariants (tuner.tools.build_note_bank).

Every stitched-sequence ground truth inherits from these clips, so the
trim/fade/normalize step must hold its contract: bounded length, uniform
peak, silent edges (no stitching clicks).
"""

import numpy as np

from tests.synth import SR, tone
from tuner.tools.build_note_bank import CLIP_MAX_S, prepare_clip


def raw_note() -> np.ndarray:
    silence = np.zeros(SR // 2)
    note = tone(220.0, 2.5, instrument="cello") * 0.3
    return np.concatenate([silence, note, silence])


def test_clip_is_trimmed_normalized_and_faded():
    clip = prepare_clip(raw_note(), SR)
    assert len(clip) <= CLIP_MAX_S * SR
    assert np.max(np.abs(clip)) == np.float64(0.7)  # uniform loudness across the bank
    assert abs(clip[0]) < 0.01 and abs(clip[-1]) < 0.01  # faded edges: no clicks
    # the leading silence must be gone: energy arrives quickly
    head = clip[: SR // 10]
    assert np.sqrt(np.mean(head**2)) > 0.05


def test_clip_keeps_the_natural_attack():
    """Trim starts slightly BEFORE the onset so the attack survives."""
    clip = prepare_clip(raw_note(), SR)
    # the very beginning is quiet (pre-onset margin), then the note rises
    first = np.sqrt(np.mean(clip[: SR // 50] ** 2))
    later = np.sqrt(np.mean(clip[SR // 10 : SR // 5] ** 2))
    assert later > first
