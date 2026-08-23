"""PortAudio-backed audio output (the one implementation for Win + macOS)."""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from tuner.audio.output import RenderCallback

# Bigger than the input's 256: this side has no latency requirement worth the
# risk of an underrun, and the metronome's timing does not depend on the block
# size at all (core/metronome.py renders from an absolute sample position, so
# the beat lands on the same sample whatever the device asks for).
BLOCK_SIZE = 512


def _default_output() -> dict:
    try:
        return sd.query_devices(sd.default.device[1], "output")
    except (sd.PortAudioError, ValueError) as error:
        raise RuntimeError(f"출력 장치가 없다: {error}") from error


class SoundDeviceOutput:
    def __init__(self) -> None:
        self._stream: sd.OutputStream | None = None
        self._sr = 0

    @property
    def sample_rate(self) -> int:
        if self._sr == 0:
            # query rather than open: the renderer has to exist before the
            # stream can pull from it, so the rate must be known first
            self._sr = int(_default_output()["default_samplerate"])
        return self._sr

    @property
    def latency_s(self) -> float:
        return float(self._stream.latency) if self._stream is not None else 0.0

    def start(self, render: RenderCallback) -> int:
        self.stop()

        def on_block(outdata: np.ndarray, frames: int, time, status) -> None:
            outdata[:, 0] = render(frames)

        try:
            self._stream = sd.OutputStream(
                channels=1,
                blocksize=BLOCK_SIZE,
                samplerate=self.sample_rate,
                callback=on_block,
            )
            self._stream.start()
        except sd.PortAudioError as error:
            # the Protocol's contract: no usable device is a condition the UI
            # reports, not a PortAudio traceback out of a button press
            self._stream = None
            raise RuntimeError(f"출력 장치를 열 수 없다: {error}") from error
        self._sr = int(self._stream.samplerate)
        return self._sr

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
