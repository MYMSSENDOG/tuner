"""Tracker policy unit tests (pure PitchResult sequences, no audio)."""

from tuner.core.pitch import PitchResult
from tuner.core.tracker import PitchTracker, State


def test_confident_pitch_passes_through():
    tracker = PitchTracker()
    out = tracker.update(PitchResult(440.0, 0.9))
    assert out.freq_hz == 440.0
    assert out.state is State.OK


def test_small_change_immediate():
    tracker = PitchTracker()
    tracker.update(PitchResult(440.0, 0.9))
    out = tracker.update(PitchResult(442.0, 0.9))
    assert out.freq_hz == 442.0


def test_single_octave_glitch_rejected():
    tracker = PitchTracker()
    tracker.update(PitchResult(440.0, 0.9))
    out = tracker.update(PitchResult(880.0, 0.9))  # 1-frame octave error
    assert out.freq_hz == 440.0
    out = tracker.update(PitchResult(440.5, 0.9))
    assert out.freq_hz == 440.5


def test_sustained_jump_followed_after_confirmation():
    tracker = PitchTracker(confirm_frames=2)
    tracker.update(PitchResult(440.0, 0.9))
    assert tracker.update(PitchResult(660.0, 0.9)).freq_hz == 440.0
    assert tracker.update(PitchResult(660.2, 0.9)).freq_hz == 660.2


def test_low_confidence_never_moves_display():
    tracker = PitchTracker()
    tracker.update(PitchResult(440.0, 0.9))
    out = tracker.update(PitchResult(123.0, 0.2))
    assert out.freq_hz == 440.0  # held


def test_brief_dropout_holds_then_releases():
    tracker = PitchTracker(hold_frames=3)
    tracker.update(PitchResult(440.0, 0.9))
    for _ in range(3):
        assert tracker.update(PitchResult(None, 0.0)).freq_hz == 440.0
    out = tracker.update(PitchResult(None, 0.0))
    assert out.freq_hz is None
    assert out.state is State.SILENT


def test_noisy_vs_silent_states():
    tracker = PitchTracker(hold_frames=0)
    assert tracker.update(PitchResult(None, 0.0)).state is State.SILENT
    assert tracker.update(PitchResult(300.0, 0.1)).state is State.NOISY
