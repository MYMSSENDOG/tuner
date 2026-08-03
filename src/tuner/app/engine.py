"""Assembles audio input -> pitch detection -> tracking -> TunerReading.

Plain Python (no Qt) so the whole pipeline is testable with a fake AudioInput.
Readings are emitted on the audio thread; the UI bridges them to its own
thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from tuner.audio.input import AudioInput
from tuner.core.detector import PitchDetector, YinDetector
from tuner.core.notes import Note, freq_to_note
from tuner.core.tracker import PitchTracker, State


@dataclass(frozen=True)
class TunerReading:
    state: State
    note: Note | None  # present iff state is OK


class TunerEngine:
    def __init__(
        self,
        audio_input: AudioInput,
        on_reading: Callable[[TunerReading], None],
        detector: PitchDetector | None = None,
    ):
        self._audio = audio_input
        self._on_reading = on_reading
        self._a4_hz = 440.0
        self._sr = 0
        self._detector: PitchDetector = detector or YinDetector()
        self._reset_pipeline()

    @property
    def a4_hz(self) -> float:
        return self._a4_hz

    def set_a4(self, a4_hz: float) -> None:
        self._a4_hz = a4_hz

    def set_detector(self, detector: PitchDetector) -> None:
        """Swap the detection algorithm. Not safe while the stream is running
        (the audio thread reads the buffer this replaces) — stop() first."""
        self._detector = detector
        self._reset_pipeline()

    def start(self, device_id: int | None = None) -> None:
        self._reset_pipeline()
        self._sr = self._audio.start(device_id, self._on_block)

    def stop(self) -> None:
        self._audio.stop()

    def _reset_pipeline(self) -> None:
        self._tracker = PitchTracker()
        self._buffer = np.zeros(self._detector.frame_size)
        self._filled = 0
        self._pending = 0

    def _on_block(self, block: np.ndarray) -> None:
        n = len(block)
        frame_size = self._detector.frame_size
        self._buffer = np.roll(self._buffer, -n)
        self._buffer[-n:] = block
        self._filled = min(self._filled + n, frame_size)
        self._pending += n
        if self._filled < frame_size or self._pending < self._detector.hop_size:
            return
        self._pending = 0
        tracked = self._tracker.update(self._detector.detect(self._buffer, self._sr))
        note = freq_to_note(tracked.freq_hz, self._a4_hz) if tracked.freq_hz else None
        self._on_reading(TunerReading(state=tracked.state, note=note))
