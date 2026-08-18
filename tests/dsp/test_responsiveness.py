"""Responsiveness: step changes, glissando tracking, scale/arpeggio classification.

Criteria (PLAN 4.2): converge on a new pitch within 100ms; follow a glissando
with <= 100ms lag and no octave jumps; classify every scale/arpeggio note
correctly outside transition transients.
"""

import numpy as np
import pytest

from tests.helpers import cents_error, track_signal
from tests.metrics import record
from tests.synth import SR, add_noise, glissando, glissando_freqs, sequence, tone
from tuner.core.notes import freq_to_note, note_to_freq

MAX_CONVERGE_S = 0.100
MAX_GLISS_LAG_S = 0.100


@pytest.mark.parametrize("step_semitones", [1, -1, 5, 12])
def test_step_change_convergence(step_semitones):
    f1 = note_to_freq("A", 4)
    f2 = f1 * 2 ** (step_semitones / 12)
    change_t = 0.5
    signal = np.concatenate([tone(f1, change_t, "violin"), tone(f2, 0.5, "violin")])

    converged_at = None
    for t, freq in track_signal(signal):
        if t <= change_t:
            continue
        if freq is not None and abs(cents_error(freq, f2)) <= 5.0:
            if converged_at is None:
                converged_at = t
        elif converged_at is not None and t < change_t + 0.3:
            converged_at = None  # must stay converged, not flicker
    assert converged_at is not None, "never converged on the new pitch"
    latency = converged_at - change_t
    print(f"\nstep {step_semitones:+d} semitones: converged in {latency * 1000:.1f}ms")
    record(f"response/step{step_semitones:+d}/converge_ms", latency * 1000, unit="ms")
    assert latency <= MAX_CONVERGE_S


@pytest.mark.parametrize("instrument", ["pure", "violin"])
def test_glissando_following(instrument):
    f_start, f_end, duration = 400.0, 800.0, 2.0
    signal = glissando(f_start, f_end, duration, instrument=instrument)
    truth = glissando_freqs(f_start, f_end, duration)

    worst_lag = 0.0
    for t, freq in track_signal(signal):
        assert freq is not None, f"lost pitch mid-glissando at {t:.3f}s"
        # displayed value corresponds to the true frequency of a slightly
        # earlier moment; that time offset is the tracking lag
        true_now = truth[min(int(t * SR), len(truth) - 1)]
        error = cents_error(freq, true_now)
        assert error <= 5.0, f"led ahead of the glissando at {t:.3f}s ({error:+.1f}c)"
        rate_cents_per_s = 1200 * np.log2(f_end / f_start) / duration
        lag = -error / rate_cents_per_s
        worst_lag = max(worst_lag, lag)
        assert lag <= MAX_GLISS_LAG_S, f"lag {lag * 1000:.0f}ms at {t:.3f}s"
    print(f"\nglissando ({instrument}): worst lag {worst_lag * 1000:.1f}ms")
    record(f"response/glissando_{instrument}/lag_ms", worst_lag * 1000, unit="ms")


D_MAJOR_ARPEGGIO = [("D", 4), ("F#", 4), ("A", 4), ("D", 5), ("F#", 5), ("A", 5), ("D", 6)]
NOTE_DURATION = 0.3
TRANSIENT_S = 0.1  # settle window after each note onset, excluded from judgment


@pytest.mark.parametrize("snr_db", [None, 10.0])
def test_arpeggio_note_classification(snr_db):
    freqs = [note_to_freq(n, o) for n, o in D_MAJOR_ARPEGGIO]
    signal = sequence(freqs, note_duration=NOTE_DURATION, gap=0.02, instrument="violin")
    if snr_db is not None:
        signal = add_noise(signal, snr_db, seed=7)

    slot = NOTE_DURATION + 0.02
    misclassified = []
    for i, ((name, octave), f_true) in enumerate(zip(D_MAJOR_ARPEGGIO, freqs)):
        judge_from = i * slot + TRANSIENT_S
        judge_to = i * slot + NOTE_DURATION - 0.01
        readings = [
            freq
            for t, freq in track_signal(signal)
            if judge_from <= t <= judge_to and freq is not None
        ]
        assert readings, f"no readings for {name}{octave}"
        for freq in readings:
            note = freq_to_note(freq)
            if (note.name, note.octave) != (name, octave):
                misclassified.append((f"{name}{octave}", freq))
    assert not misclassified, f"misclassified readings: {misclassified}"
