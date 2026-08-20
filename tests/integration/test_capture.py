"""Field capture: the rolling window, the report it writes, and promotion.

The point of a report is that it replays: what the meter showed live has to
come back frame for frame when the saved audio goes through the pipeline
again. That is what makes a complaint into a fixture, so it is what these
tests pin.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf

from tests.fakes import FakeAudioInput
from tests.synth import SR, sequence, tone
from tuner.app.capture import FieldCapture, _retimed, reports_dir
from tuner.app.engine import TunerEngine
from tuner.core.notes import note_to_freq
from tuner.tools.promote import load_report, promote, reproduce
from tuner.tools.trace import trace_signal


def two_notes() -> np.ndarray:
    return sequence([note_to_freq("A", 4), note_to_freq("C", 5)], 0.4, instrument="violin")


def run_engine(
    signal: np.ndarray, capture: FieldCapture, sr: int = SR, a4_hz: float = 440.0
) -> None:
    fake = FakeAudioInput(signal, sr=sr)
    engine = TunerEngine(fake, lambda reading: None, capture=capture)
    engine.set_a4(a4_hz)
    engine.start()
    fake.pump()
    engine.stop()


def test_ring_keeps_the_last_seconds_in_order():
    capture = FieldCapture(seconds=0.1)  # 4410 samples at SR
    signal = np.arange(SR // 2, dtype=float)  # 0.5s of a ramp: wraps 5x
    for start in range(0, len(signal), 256):
        capture.push_block(signal[start : start + 256], SR)

    held, sr, _ = capture.snapshot()
    assert sr == SR and len(held) == int(0.1 * SR)
    assert np.array_equal(held, signal[-len(held) :])


def test_ring_reports_nothing_before_any_audio():
    assert FieldCapture().snapshot() is None
    with pytest.raises(RuntimeError):
        FieldCapture().save()


def test_capture_holds_a_whole_short_signal():
    capture = FieldCapture(seconds=10.0)
    signal = two_notes()
    run_engine(signal, capture)

    held, _sr, frames = capture.snapshot()
    assert np.allclose(held, signal[: len(held)])
    assert frames and frames[0].t_s < 0.1  # window starts at the beginning


def test_live_trace_equals_the_offline_one():
    """The live capture and the tracer must agree — otherwise a report could
    never be checked against a replay, which is the whole workflow."""
    capture = FieldCapture(seconds=10.0)
    signal = two_notes()
    run_engine(signal, capture)

    _, _, frames = capture.snapshot()
    offline = trace_signal(signal, SR)
    assert len(frames) == len(offline.frames)
    for live, replayed in zip(frames, offline.frames, strict=True):
        assert (live.label, live.cents, live.state) == (
            replayed.label,
            replayed.cents,
            replayed.state,
        )


def test_old_frames_drop_out_with_the_audio():
    capture = FieldCapture(seconds=0.3)
    run_engine(tone(note_to_freq("A", 4), 1.5, instrument="violin"), capture)

    held, _sr, frames = capture.snapshot()
    assert len(held) == int(0.3 * SR)
    assert frames, "the window must still carry its own readings"
    # retimed to the window, not to the session
    assert 0.0 <= frames[0].t_s <= 0.3 and frames[-1].t_s <= 0.31


def test_saved_report_replays_frame_for_frame(tmp_path):
    capture = FieldCapture(seconds=10.0)
    # the report records the reference the engine ran with: replaying under a
    # different A4 shifts every reading (2Hz = 7.9 cents) and the diff says so
    run_engine(two_notes(), capture, a4_hz=442.0)
    directory = capture.save(detector="YIN (fast)", a4_hz=442.0, into=tmp_path)

    meta, captured, audio = load_report(directory)
    assert audio.exists() and (directory / "trace.jsonl").exists()
    assert meta["detector"] == "YIN (fast)" and meta["a4_hz"] == 442.0
    assert meta["rev"] and meta["sr"] == SR

    _, replayed, spans = reproduce(directory)
    assert spans == [], f"a report that cannot be replayed is not evidence: {spans}"
    assert len(replayed.frames) == len(captured.frames)


def test_two_reports_in_the_same_second_do_not_collide(tmp_path):
    capture = FieldCapture(seconds=10.0)
    run_engine(tone(note_to_freq("A", 4), 0.3), capture)
    first = capture.save(into=tmp_path)
    second = capture.save(into=tmp_path)
    assert first != second and first.exists() and second.exists()


def test_reports_dir_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TUNER_REPORTS_DIR", str(tmp_path / "elsewhere"))
    assert reports_dir() == tmp_path / "elsewhere"
    monkeypatch.delenv("TUNER_REPORTS_DIR")
    assert reports_dir().parts[-2:] == (".tuner", "reports")


def test_promote_copies_and_labels(tmp_path):
    capture = FieldCapture(seconds=10.0)
    run_engine(two_notes(), capture)
    directory = capture.save(into=tmp_path)
    _, _, audio = load_report(directory)

    fixtures = tmp_path / "fixtures"
    target = promote(audio, "violin_field_A4", into=fixtures)
    assert target == fixtures / "violin_field_A4.wav"
    labels = json.loads((fixtures / "violin_field_A4.ref.json").read_text())
    assert any(w["freq_hz"] for w in labels["windows"])

    with pytest.raises(SystemExit):  # never silently overwrite the corpus
        promote(audio, "violin_field_A4", annotate=False, into=fixtures)


def test_engine_runs_without_a_capture():
    readings = []
    fake = FakeAudioInput(tone(note_to_freq("A", 4), 0.3))
    engine = TunerEngine(fake, readings.append)
    engine.start()
    fake.pump()
    engine.stop()
    assert readings and engine._capture is None


# --- session recording: the button-driven half, where nothing is dropped ---


def test_session_keeps_everything_past_the_ring(tmp_path):
    """The ring holds 10 seconds; a session must not be bounded by it."""
    capture = FieldCapture(seconds=0.2)  # a deliberately tiny ring
    signal = tone(note_to_freq("A", 4), 2.0, instrument="violin")
    fake = FakeAudioInput(signal)
    engine = TunerEngine(fake, lambda r: None, capture=capture)
    engine.start()
    capture.start_recording()
    fake.pump()
    engine.stop()

    assert capture.recording and capture.recorded_seconds == pytest.approx(2.0, abs=0.01)
    directory = capture.save_recording(into=tmp_path)
    assert not capture.recording

    meta, trace, audio = load_report(directory)
    assert meta["kind"] == "session" and meta["seconds"] == pytest.approx(2.0, abs=0.01)
    assert len(sf.read(audio)[0]) == len(signal)
    # a session of 2s holds ~10x what the 0.2s ring could
    assert len(trace.frames) > 300


def test_session_starts_where_the_button_was_pressed():
    capture = FieldCapture(seconds=10.0)
    fake = FakeAudioInput(tone(note_to_freq("A", 4), 1.0, instrument="violin"))
    engine = TunerEngine(fake, lambda r: None, capture=capture)
    engine.start()
    for i, start in enumerate(range(0, len(fake._signal), 256)):
        if i == 100:  # press record part-way through
            capture.start_recording()
        engine._on_block(fake._signal[start : start + 256])
    engine.stop()

    _, _, ring_frames = capture.snapshot()
    directory_frames = capture._session_frames
    assert len(directory_frames) < len(ring_frames)  # only what came after
    assert _retimed(directory_frames, capture._session_start / SR)[0].t_s < 0.02


def test_a_restart_ends_the_recording_rather_than_corrupting_it(tmp_path):
    """Frame times are counted from the stream's start, so a stream restart
    (a device change) breaks the session's clock. Keep what we have, say so."""
    capture = FieldCapture(seconds=10.0)
    run_engine(tone(note_to_freq("A", 4), 0.3), capture)
    capture.start_recording()
    capture.push_block(np.zeros(256), SR)
    capture.start(48000)  # the device changed

    assert not capture.recording and capture.interrupted
    meta, _, _ = load_report(capture.save_recording(into=tmp_path))
    assert meta["interrupted"] is True


def test_cancel_throws_the_session_away():
    capture = FieldCapture(seconds=10.0)
    run_engine(tone(note_to_freq("A", 4), 0.3), capture)
    capture.start_recording()
    capture.push_block(np.zeros(512), SR)
    capture.cancel_recording()
    assert not capture.recording and capture.recorded_seconds == 0.0
    with pytest.raises(RuntimeError):
        capture.save_recording()
