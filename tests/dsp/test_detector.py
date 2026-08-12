"""Both detector implementations must satisfy the interface and base accuracy."""

import time

import pytest

from tests.helpers import cents_error
from tests.synth import SR, tone
from tuner.core.detector import DETECTORS, SpectralDetector, YinDetector
from tuner.core.notes import note_to_freq


@pytest.mark.parametrize("detector_cls", DETECTORS, ids=lambda c: c.__name__)
def test_detector_accuracy(detector_cls):
    detector = detector_cls()
    for name, octave in [("G", 3), ("A", 4), ("D", 6)]:
        freq = note_to_freq(name, octave)
        signal = tone(freq, 0.5, instrument="violin")
        mid = len(signal) // 2
        result = detector.detect(signal[mid : mid + detector.frame_size], SR)
        assert result.freq_hz is not None
        assert result.confidence >= 0.5
        assert abs(cents_error(result.freq_hz, freq)) <= 2.0


@pytest.mark.parametrize("detector_cls", DETECTORS, ids=lambda c: c.__name__)
def test_detector_realtime_budget(detector_cls):
    """One detection must fit well inside the detector's own hop interval."""
    detector = detector_cls()
    signal = tone(440.0, 1.0, instrument="violin")
    frame = signal[: detector.frame_size]
    detector.detect(frame, SR)  # warm-up
    start = time.perf_counter()
    n = 20
    for _ in range(n):
        detector.detect(frame, SR)
    per_call = (time.perf_counter() - start) / n
    budget = detector.hop_size / SR
    print(f"\n{detector_cls.__name__}: {per_call * 1000:.2f}ms per detection "
          f"(budget {budget * 1000:.1f}ms)")
    assert per_call < budget * 0.5


@pytest.mark.parametrize("detector_cls", DETECTORS, ids=lambda c: c.__name__)
def test_input_level_gate(detector_cls):
    """Quieter than -40dBFS is 'not being played at': bow tails and room
    rumble carry trackable pitch and flip the display without a gate."""
    detector = detector_cls()
    quiet = tone(440.0, 0.2, instrument="violin") * 10 ** (-50 / 20)
    frame = quiet[: detector.frame_size]
    assert detector.detect(frame, SR).freq_hz is None

    audible = tone(440.0, 0.2, instrument="violin") * 10 ** (-30 / 20)
    result = detector.detect(audible[: detector.frame_size], SR)
    assert result.freq_hz is not None


def test_engine_accepts_spectral_detector():
    from tests.fakes import FakeAudioInput
    from tuner.app.engine import TunerEngine
    from tuner.core.tracker import State

    readings = []
    fake = FakeAudioInput(tone(440.0, 0.5, instrument="violin"))
    engine = TunerEngine(fake, readings.append, detector=SpectralDetector())
    engine.start()
    fake.pump()
    engine.stop()
    ok = [r for r in readings if r.state is State.OK]
    assert ok and ok[-1].note.label == "A4"
    assert abs(ok[-1].note.cents) <= 0.5


def test_engine_detector_hotswap():
    from tests.fakes import FakeAudioInput
    from tuner.app.engine import TunerEngine
    from tuner.core.tracker import State

    readings = []
    fake = FakeAudioInput(tone(440.0, 0.5, instrument="violin"))
    engine = TunerEngine(fake, readings.append, detector=YinDetector())
    engine.start()
    fake.pump()
    # contract: the stream must be stopped while the detector is swapped
    engine.stop()
    engine.set_detector(SpectralDetector())
    engine.start()
    fake.pump()
    engine.stop()
    ok = [r for r in readings if r.state is State.OK]
    assert ok and ok[-1].note.label == "A4"
