"""Display smoothness: jitter on stitched real sequences, vibrato preservation.

Regression gates for the smoothing defaults chosen from the measured sweep
(docs/smoothing-tuning.md). Baseline without smoothing: jitter p50 0.21c /
p95 0.94c, vibrato ratio 0.95.
"""

import numpy as np
import pytest

from tests.helpers import cents_error, track_signal
from tests.sequence_bank import BANK_DIR, build_sequence, major_scale, twinkle
from tests.synth import SR, tone
from tuner.core.notes import note_to_freq

requires_bank = pytest.mark.skipif(
    not (BANK_DIR / "bank.json").exists(),
    reason="note bank not built (tuner.tools.build_note_bank)",
)

MAX_JITTER_P50 = 0.15  # cents per reading; tuned value ~0.10
MAX_JITTER_P95 = 0.70  # tuned value ~0.52
MIN_VIBRATO_RATIO = 0.75  # tuned value ~0.85


@requires_bank
def test_jitter_on_sustained_notes():
    deltas = []
    for instrument, melody in (
        ("violin", major_scale("G4")),
        ("flute", twinkle("C5")),
        ("trumpet", major_scale("C4")),
    ):
        signal, sr, ref = build_sequence(instrument, melody)
        prev = None
        for t, f in track_signal(signal, sr):
            i = int((t - 1024 / sr) / 0.05)
            stable = 0 <= i < len(ref) and ref[i].freq_hz is not None
            if f is None or not stable:
                prev = None
                continue
            if prev is not None:
                deltas.append(abs(cents_error(f, prev)))
            prev = f
    d = np.array(deltas)
    p50, p95 = float(np.percentile(d, 50)), float(np.percentile(d, 95))
    print(f"\njitter: n={len(d)} p50={p50:.3f}c p95={p95:.3f}c")
    assert p50 <= MAX_JITTER_P50
    assert p95 <= MAX_JITTER_P95


def test_vibrato_amplitude_preserved():
    """Smoothing must not iron out real pitch motion: a +-20c vibrato should
    survive with most of its displayed amplitude."""
    freq = note_to_freq("A", 4)
    signal = tone(freq, 2.0, instrument="violin", vibrato_cents=20.0)
    cents = [
        cents_error(f, freq)
        for t, f in track_signal(signal, SR)
        if f is not None and t > 0.3
    ]
    peak_to_peak = np.percentile(cents, 98) - np.percentile(cents, 2)
    ratio = peak_to_peak / 40.0
    print(f"\nvibrato ratio: {ratio:.2f}")
    assert ratio >= MIN_VIBRATO_RATIO
