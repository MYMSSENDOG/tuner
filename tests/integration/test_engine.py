"""End-to-end pipeline test with a fake audio input (no real device)."""

import numpy as np

from tuner.app.engine import TunerEngine
from tuner.core.tracker import State

from tests.fakes import FakeAudioInput
from tests.synth import SR, tone


def run_engine(signal: np.ndarray, a4_hz: float = 440.0):
    readings = []
    fake = FakeAudioInput(signal)
    engine = TunerEngine(fake, readings.append)
    engine.set_a4(a4_hz)
    engine.start()
    fake.pump()
    engine.stop()
    return readings


def test_tone_produces_correct_reading():
    readings = run_engine(tone(440.0, 0.5, instrument="violin"))
    ok = [r for r in readings if r.state is State.OK]
    assert len(ok) > 10
    last = ok[-1]
    assert last.note.label == "A4"
    assert abs(last.note.cents) <= 1.0


def test_a4_reference_applied():
    readings = run_engine(tone(442.0, 0.5), a4_hz=442.0)
    last = [r for r in readings if r.state is State.OK][-1]
    assert last.note.label == "A4"
    assert abs(last.note.cents) <= 1.0


def test_silence_reports_silent():
    readings = run_engine(np.zeros(SR // 2))
    assert readings
    assert all(r.state is State.SILENT and r.note is None for r in readings)


def test_engine_restarts_on_new_device():
    fake = FakeAudioInput()
    engine = TunerEngine(fake, lambda r: None)
    engine.start(1)
    engine.stop()
    engine.start(2)
    engine.stop()
    assert fake.started_with == [1, 2]
    assert fake.stop_count == 2
