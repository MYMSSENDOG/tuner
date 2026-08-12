"""File playback that feeds analysis taps the exact samples being heard.

One place for the chunk-serving state machine (end-of-file padding, loop
wraparound) that both the single-pane demo and the multi-pane compare tool
need — previously duplicated in each, tested in neither.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd
import soundfile as sf

from tuner.audio.input import BlockCallback, InputDevice

BLOCK_SIZE = 256


class SharedPlayback:
    """One output stream, many analysis taps: every consumer hears and
    analyses the exact same blocks."""

    def __init__(self, path: str, loop: bool = False):
        signal, sr = sf.read(path, always_2d=True)
        self._signal = np.ascontiguousarray(signal.mean(axis=1), dtype=np.float32)
        self.sr = sr
        self._loop = loop
        self._pos = 0
        self._taps: list[BlockCallback] = []
        self._stream: sd.OutputStream | None = None

    def next_chunk(self, frames: int) -> np.ndarray:
        """Advance playback by exactly `frames` samples.

        Past the end the chunk is zero-padded (the tuner then hears silence);
        in loop mode it wraps and stays gapless. Pure state + arithmetic, so
        the behavior is unit-testable without any audio device.
        """
        chunk = self._signal[self._pos : self._pos + frames]
        self._pos += frames
        if len(chunk) < frames:
            if self._loop and len(self._signal) > 0:
                self._pos = frames - len(chunk)
                chunk = np.concatenate([chunk, self._signal[: self._pos]])
            else:
                chunk = np.concatenate([chunk, np.zeros(frames - len(chunk), dtype=np.float32)])
        return chunk

    def add_tap(self, callback: BlockCallback) -> None:
        self._taps.append(callback)

    def start(self) -> None:
        if self._stream is not None:
            return

        def on_block(outdata: np.ndarray, frames: int, time, status) -> None:
            chunk = self.next_chunk(frames)
            outdata[:, 0] = chunk
            block = chunk.astype(np.float64)
            for tap in self._taps:
                tap(block)

        self._stream = sd.OutputStream(
            samplerate=self.sr, channels=1, blocksize=BLOCK_SIZE, callback=on_block
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class PlaybackTap:
    """AudioInput view of SharedPlayback for one engine.

    Registration only — the owner starts the stream once every tap is wired,
    so no tap misses the beginning and the tap list never mutates while the
    audio callback iterates it."""

    def __init__(self, shared: SharedPlayback):
        self._shared = shared

    def list_devices(self) -> list[InputDevice]:
        return []

    def start(self, device_id: int | None, callback: BlockCallback) -> int:
        self._shared.add_tap(callback)
        return self._shared.sr

    def stop(self) -> None:
        self._shared.stop()

    def refresh_devices(self) -> None:
        pass  # the source cannot gain devices


class FilePlaybackInput(PlaybackTap):
    """Single-consumer convenience: starting the engine starts playback."""

    def __init__(self, path: str, loop: bool = False):
        super().__init__(SharedPlayback(path, loop=loop))

    def start(self, device_id: int | None, callback: BlockCallback) -> int:
        sr = super().start(device_id, callback)
        self._shared.start()
        return sr
