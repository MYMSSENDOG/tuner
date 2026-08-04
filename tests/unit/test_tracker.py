"""Tracker policy unit tests (pure PitchResult sequences, no audio)."""

from tuner.core.pitch import PitchResult
from tuner.core.tracker import PitchTracker, State


def policy_tracker(**kwargs):
    """Smoothing off: these tests pin the jump/hold/confidence policy, and
    exact-value assertions need the raw pass-through path."""
    return PitchTracker(smooth_min_cutoff_hz=None, **kwargs)


def test_confident_pitch_passes_through():
    tracker = policy_tracker()
    out = tracker.update(PitchResult(440.0, 0.9))
    assert out.freq_hz == 440.0
    assert out.state is State.OK


def test_small_change_immediate():
    tracker = policy_tracker()
    tracker.update(PitchResult(440.0, 0.9))
    out = tracker.update(PitchResult(442.0, 0.9))
    assert out.freq_hz == 442.0


def test_single_octave_glitch_rejected():
    tracker = policy_tracker()
    tracker.update(PitchResult(440.0, 0.9))
    out = tracker.update(PitchResult(880.0, 0.9))  # 1-frame octave error
    assert out.freq_hz == 440.0
    out = tracker.update(PitchResult(440.5, 0.9))
    assert out.freq_hz == 440.5


def test_sustained_jump_followed_after_confirmation():
    tracker = policy_tracker(confirm_frames=2)
    tracker.update(PitchResult(440.0, 0.9))
    assert tracker.update(PitchResult(660.0, 0.9)).freq_hz == 440.0
    assert tracker.update(PitchResult(660.2, 0.9)).freq_hz == 660.2


def test_low_confidence_never_moves_display():
    tracker = policy_tracker()
    tracker.update(PitchResult(440.0, 0.9))
    out = tracker.update(PitchResult(123.0, 0.2))
    assert out.freq_hz == 440.0  # held


def test_brief_dropout_holds_then_releases():
    tracker = policy_tracker(hold_frames=3)
    tracker.update(PitchResult(440.0, 0.9))
    for _ in range(3):
        assert tracker.update(PitchResult(None, 0.0)).freq_hz == 440.0
    out = tracker.update(PitchResult(None, 0.0))
    assert out.freq_hz is None
    assert out.state is State.SILENT


def test_noisy_vs_silent_states():
    tracker = policy_tracker(hold_frames=0)
    assert tracker.update(PitchResult(None, 0.0)).state is State.SILENT
    assert tracker.update(PitchResult(300.0, 0.1)).state is State.NOISY


class TestSmoothing:
    def test_steady_jitter_reduced(self):
        import numpy as np

        rng = np.random.default_rng(0)
        noisy = 440.0 * 2 ** (rng.normal(0, 1.0, 300) / 1200)  # 1c rms jitter
        raw_out, smooth_out = [], []
        raw, smooth = policy_tracker(), PitchTracker()
        for f in noisy:
            raw_out.append(raw.update(PitchResult(float(f), 0.9)).freq_hz)
            smooth_out.append(smooth.update(PitchResult(float(f), 0.9)).freq_hz)
        var = lambda xs: np.std(np.diff([1200 * np.log2(x / 440) for x in xs[50:]]))
        assert var(smooth_out) < 0.5 * var(raw_out)

    def test_confirmed_jump_snaps_instantly(self):
        tracker = PitchTracker(confirm_frames=2)
        for _ in range(20):
            tracker.update(PitchResult(440.0, 0.9))
        tracker.update(PitchResult(660.0, 0.9))
        out = tracker.update(PitchResult(660.0, 0.9))
        assert out.freq_hz == 660.0  # no smoothing ramp across a note change

    def test_smoothing_converges_on_sustained_value(self):
        tracker = PitchTracker()
        tracker.update(PitchResult(440.0, 0.9))
        for _ in range(200):
            out = tracker.update(PitchResult(442.0, 0.9))
        assert abs(out.freq_hz - 442.0) < 0.05
