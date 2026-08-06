"""Ground truth that owes nothing to our code: a tone of exactly known
frequency, played through the speakers and recorded through the microphone.

Every other real-audio test grades against annotations we computed. This one
grades against arithmetic: an acoustic path changes level and timbre, adds
room reflections and background noise, but it cannot change frequency. So
the played frequency IS the truth, while the signal reaching the detector is
genuinely real-world — the only test here that exercises speakers, room and
microphone at once.

Needs a machine with both output and input, and it listens to the room, so
it is e2e and skips itself when unavailable. Keep the volume audible.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

sd = pytest.importorskip("sounddevice")

from tuner.audio.sounddevice_input import SoundDeviceInput  # noqa: E402
from tuner.core.detector import YinDetector  # noqa: E402
from tuner.core.notes import note_to_freq  # noqa: E402
from tuner.core.tracker import PitchTracker  # noqa: E402

pytestmark = pytest.mark.e2e

PLAY_SECONDS = 2.5
# generous: a laptop speaker/mic pair in an untreated room, with whatever
# noise the room has. Octave correctness and "within a few cents" is the
# claim being tested, not the sub-cent accuracy proven on clean signals.
MAX_ERROR_CENTS = 15.0


SPEAKER_HINTS = ("speaker", "스피커", "internal", "built-in", "내장")


def _speaker_device() -> int | None:
    """Prefer a real loudspeaker over the default output: if the default is
    headphones or earbuds there is no acoustic path to the microphone and
    this test can only skip."""
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0 and any(h in d["name"].lower() for h in SPEAKER_HINTS):
            return i
    default = sd.default.device[1]
    return default if default is not None and default >= 0 else None


def _devices_available() -> bool:
    try:
        return bool(SoundDeviceInput().list_devices()) and _speaker_device() is not None
    except Exception:
        return False


requires_audio = pytest.mark.skipif(
    not _devices_available(), reason="needs both speaker and microphone"
)


def harmonic_tone(freq_hz: float, seconds: float, sr: int) -> np.ndarray:
    """A few harmonics, so the mic picks up something instrument-like rather
    than a pure tone that room modes can swallow."""
    t = np.arange(int(seconds * sr)) / sr
    wave = sum(amp * np.sin(2 * np.pi * freq_hz * k * t) for k, amp in enumerate((1.0, 0.5, 0.25), 1))
    envelope = np.minimum(1.0, np.minimum(t, seconds - t) / 0.05)
    return (0.25 * wave / np.max(np.abs(wave)) * envelope).astype(np.float32)


@requires_audio
@pytest.mark.parametrize("note,octave", [("A", 4), ("D", 5)])
def test_detects_tone_played_into_the_room(note, octave):
    truth_hz = note_to_freq(note, octave)
    detector, tracker = YinDetector(), PitchTracker()
    captured: list[np.ndarray] = []

    audio = SoundDeviceInput()
    sr = audio.start(None, captured.append)
    try:
        sd.play(
            harmonic_tone(truth_hz, PLAY_SECONDS, sr), sr,
            device=_speaker_device(), blocking=False,
        )
        time.sleep(PLAY_SECONDS)
    finally:
        sd.stop()
        audio.stop()

    signal = np.concatenate(captured) if captured else np.zeros(0)
    assert len(signal) > sr, "microphone delivered almost nothing"
    if float(np.sqrt(np.mean(signal**2))) == 0.0:
        # macOS hands out digital silence instead of failing when the process
        # lacks microphone permission (System Settings > Privacy > Microphone)
        pytest.skip("microphone returned pure silence — grant it mic permission")

    # judge the middle of the take: the edges hold fade-in/out and latency
    middle = signal[len(signal) // 4 : -len(signal) // 4]
    readings = []
    for start in range(0, len(middle) - detector.frame_size, detector.hop_size):
        tracked = tracker.update(detector.detect(middle[start : start + detector.frame_size], sr))
        if tracked.freq_hz:
            readings.append(tracked.freq_hz)

    if len(readings) < 20:
        pytest.skip("speaker output never reached the microphone (muted or too quiet)")

    errors = np.array([1200 * math.log2(f / truth_hz) for f in readings])
    median = float(np.median(errors))
    print(
        f"\n{note}{octave} through speaker+room+mic: {len(readings)} readings, "
        f"median {median:+.1f}c, p90 |err| {np.percentile(np.abs(errors), 90):.1f}c"
    )
    assert abs(median) <= MAX_ERROR_CENTS
    assert np.mean(np.abs(errors) > 300) < 0.10  # essentially no octave errors
