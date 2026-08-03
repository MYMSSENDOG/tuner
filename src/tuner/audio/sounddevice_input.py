"""PortAudio-backed audio input (the one implementation for Win + macOS)."""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from tuner.audio.input import BlockCallback, InputDevice

BLOCK_SIZE = 256  # samples per callback = detection hop size


class SoundDeviceInput:
    def __init__(self) -> None:
        self._stream: sd.InputStream | None = None

    def list_devices(self) -> list[InputDevice]:
        default_id = sd.default.device[0]
        # Windows exposes each physical device through several host APIs
        # (MME/WASAPI/DirectSound); list only the default host API to avoid
        # duplicates. macOS has a single host API so this is a no-op there.
        devices = sd.query_devices()
        return [
            InputDevice(id=d["index"], name=d["name"], is_default=d["index"] == default_id)
            for d in devices
            if d["max_input_channels"] > 0 and d["hostapi"] == sd.default.hostapi
        ]

    def start(self, device_id: int | None, callback: BlockCallback) -> int:
        self.stop()

        def on_block(indata: np.ndarray, frames: int, time, status) -> None:
            callback(indata[:, 0].astype(np.float64))

        self._stream = sd.InputStream(
            device=device_id,
            channels=1,
            blocksize=BLOCK_SIZE,
            callback=on_block,
        )
        self._stream.start()
        return int(self._stream.samplerate)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
