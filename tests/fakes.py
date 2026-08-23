"""Test doubles for the audio layer."""

from __future__ import annotations

import numpy as np

from tests.synth import SR
from tuner.audio.input import InputDevice


class FakeAudioInput:
    """AudioInput double: delivers a prepared signal in blocks when pumped,
    and records every start/stop call for interaction assertions."""

    def __init__(
        self,
        signal: np.ndarray | None = None,
        block_size: int = 256,
        devices: tuple[InputDevice, ...] = (),
        sr: int = SR,
    ):
        self._signal = signal if signal is not None else np.zeros(0)
        self._block_size = block_size
        self._devices = list(devices)
        self._sr = sr
        self._callback = None
        self.started_with: list[int | None] = []
        self.stop_count = 0

    def list_devices(self) -> list[InputDevice]:
        return self._devices

    def start(self, device_id, callback) -> int:
        self.started_with.append(device_id)
        self._callback = callback
        return self._sr

    def stop(self) -> None:
        self.stop_count += 1
        self._callback = None

    def refresh_devices(self) -> None:
        pass  # the source cannot gain devices

    def pump(self, before_block=None) -> None:
        """Deliver the whole signal. `before_block` is handed the sample index
        of the block about to arrive — enough to drive a fake clock in step
        with the audio, which is what interference suppression is judged on.
        """
        for start in range(0, len(self._signal), self._block_size):
            if before_block is not None:
                before_block(start)
            self._callback(self._signal[start : start + self._block_size])


class FakeAudioOutput:
    """AudioOutput double: renders on demand instead of on a device thread,
    so a test can advance the metronome by an exact number of samples."""

    def __init__(self, sr: int = SR, latency_s: float = 0.0):
        self._sr = sr
        self._latency_s = latency_s
        self._render = None
        self.start_count = 0
        self.stop_count = 0

    @property
    def sample_rate(self) -> int:
        return self._sr

    @property
    def latency_s(self) -> float:
        return self._latency_s

    def start(self, render) -> int:
        self.start_count += 1
        self._render = render
        return self._sr

    def stop(self) -> None:
        self.stop_count += 1
        self._render = None

    def pull(self, frames: int) -> np.ndarray:
        """One device callback's worth of output."""
        assert self._render is not None, "pulled from a stopped output"
        return self._render(frames)
