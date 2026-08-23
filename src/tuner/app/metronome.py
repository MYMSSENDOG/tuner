"""Assembles the metronome core onto an audio output device.

Thin on purpose, the same way app/engine.py is: core/metronome.py owns every
decision about where a beat goes, audio/output.py owns the device, and this
only wires the pull callback and publishes when each click became audible so
the tuner can look away (core/interference.py).

Nothing here runs on a Qt timer. The device asks for samples, the metronome
renders them from an absolute sample position, and the beat is therefore as
steady as the sound card's clock rather than as steady as the GUI.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np

from tuner.audio.output import AudioOutput
from tuner.core.interference import HeardClicks
from tuner.core.metronome import DEFAULT_BPM, Metronome, clamp_bpm


class MetronomeService:
    """Start/stop and tempo, plus the click timeline the tuner reads."""

    def __init__(
        self,
        output: AudioOutput,
        bpm: float = DEFAULT_BPM,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._output = output
        self._clock = clock
        self._bpm = clamp_bpm(bpm)
        self._metronome: Metronome | None = None
        self._running = False
        # handed to the tuner engine once, at construction. It is told the
        # tempo and nothing else - where the beat actually falls it works out
        # from the microphone, which is why a wrong latency figure or a
        # drifting output clock cannot aim it wrongly (docs/metronome.md).
        self.clicks = HeardClicks()

    @property
    def bpm(self) -> float:
        return self._bpm

    @property
    def running(self) -> bool:
        return self._running

    def set_bpm(self, bpm: float) -> float:
        """Change tempo, mid-beat if need be. Returns the value actually taken
        (the request is clamped, never rejected)."""
        self._bpm = clamp_bpm(bpm)
        if self._metronome is not None:
            self._metronome.set_bpm(self._bpm)
            self.clicks.set_period(60.0 / self._bpm)
        return self._bpm

    def start(self) -> None:
        if self._running:
            return
        # built for the rate the device will actually open at, which is why
        # AudioOutput can be asked for it before the stream exists
        self._metronome = Metronome(self._output.sample_rate, self._bpm)
        self._running = True
        self.clicks.set_period(60.0 / self._bpm)
        self._output.start(self._render)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._output.stop()
        self._metronome = None
        self.clicks.idle()  # nothing to look for; suppress nothing

    def toggle(self) -> bool:
        self.stop() if self._running else self.start()
        return self._running

    def _render(self, frames: int) -> np.ndarray:
        """Audio thread: the device's pull. Nothing is published from here —
        when the beat is audible is the microphone's business, not ours."""
        metronome = self._metronome
        if metronome is None:
            return np.zeros(frames)
        return metronome.render(frames)
