"""Test doubles for the audio layer."""

from __future__ import annotations

import numpy as np

from tuner.audio.input import InputDevice

from tests.synth import SR


class FakeAudioInput:
    """AudioInput double: delivers a prepared signal in blocks when pumped,
    and records every start/stop call for interaction assertions."""

    def __init__(
        self,
        signal: np.ndarray | None = None,
        block_size: int = 256,
        devices: tuple[InputDevice, ...] = (),
    ):
        self._signal = signal if signal is not None else np.zeros(0)
        self._block_size = block_size
        self._devices = list(devices)
        self._callback = None
        self.started_with: list[int | None] = []
        self.stop_count = 0

    def list_devices(self) -> list[InputDevice]:
        return self._devices

    def start(self, device_id, callback) -> int:
        self.started_with.append(device_id)
        self._callback = callback
        return SR

    def stop(self) -> None:
        self.stop_count += 1
        self._callback = None

    def pump(self) -> None:
        for start in range(0, len(self._signal), self._block_size):
            self._callback(self._signal[start : start + self._block_size])
