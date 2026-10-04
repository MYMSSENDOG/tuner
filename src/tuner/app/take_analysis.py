"""What the meter would have shown, for a whole saved take.

A recording heard back is half the answer to "how was that?"; the other half
is where it went sharp or flat. This runs a take through the tuner's own
engine — the same detector, tracker and note latch the meter uses — and keeps
every reading, so the playback page can draw the intonation of the whole take
under the sound of it.

It is the engine and not a separate estimator on purpose: the graph should
agree with what the needle would have done, not be a second opinion.

Speed: measured at about 3x real time (20s of audio in 6.35s), so a long take
takes a while. The caller runs this off the GUI thread; `on_progress` lets it
draw the graph as it fills in, and `should_stop` lets it give up when the
player opens a different take.

Qt-free, like the engine it drives.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from tuner.app.engine import TunerEngine, TunerReading
from tuner.core.tracker import State

BLOCK = 256
# How often progress is reported, in seconds of audio. Often enough that the
# graph visibly grows; rarely enough that redraws do not cost more than the
# analysis.
PROGRESS_EVERY_S = 1.0


@dataclass
class PitchTrack:
    """One reading per detection: when, how far off, and which note."""

    duration_s: float
    times: list[float] = field(default_factory=list)
    cents: list[float] = field(default_factory=list)  # NaN when nothing was shown
    labels: list[str | None] = field(default_factory=list)
    complete: bool = False

    @property
    def analysed_s(self) -> float:
        return self.times[-1] if self.times else 0.0

    def copy(self) -> PitchTrack:
        return PitchTrack(
            self.duration_s, list(self.times), list(self.cents), list(self.labels), self.complete
        )


class _OfflineInput:
    """An AudioInput the engine can be fed from a signal in memory."""

    def __init__(self, sr: int):
        self._sr = sr
        self._callback = None

    def list_devices(self):
        return []

    def refresh_devices(self) -> None:
        pass

    def start(self, device_id, callback) -> int:
        self._callback = callback
        return self._sr

    def stop(self) -> None:
        self._callback = None

    def push(self, block: np.ndarray) -> None:
        if self._callback is not None:
            self._callback(block)


def analyse(
    signal: np.ndarray,
    sr: int,
    a4_hz: float,
    on_progress: Callable[[PitchTrack], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> PitchTrack | None:
    """The take's readings, or None if `should_stop` said to give up."""
    signal = np.asarray(signal, dtype=np.float64)
    track = PitchTrack(duration_s=len(signal) / sr if sr else 0.0)
    fed = 0

    def on_reading(reading: TunerReading) -> None:
        track.times.append(fed / sr)
        if reading.state is State.OK and reading.note is not None:
            track.cents.append(reading.note.cents)
            track.labels.append(reading.note.label)
        else:
            track.cents.append(math.nan)
            track.labels.append(None)

    source = _OfflineInput(sr)
    engine = TunerEngine(source, on_reading)
    engine.set_a4(a4_hz)
    engine.start()
    next_report = PROGRESS_EVERY_S * sr
    try:
        for start in range(0, len(signal), BLOCK):
            if should_stop is not None and should_stop():
                return None
            block = signal[start : start + BLOCK]
            fed += len(block)
            source.push(block)
            if on_progress is not None and fed >= next_report:
                on_progress(track.copy())
                next_report += PROGRESS_EVERY_S * sr
    finally:
        engine.stop()
    track.complete = True
    if on_progress is not None:
        on_progress(track.copy())
    return track
