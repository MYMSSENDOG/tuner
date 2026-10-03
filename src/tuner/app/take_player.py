"""Playing a saved take back through the output device.

The list would be half a feature without it: the reason to keep takes is to
listen to them, and sending the player out to a file manager to do that is
sending them away from the tuner.

It reuses the `AudioOutput` seam the metronome already defined — the device
pulls, we render — so this is drivable by `FakeAudioOutput` in tests exactly
as the metronome is, with no sound card anywhere.

Resampling is a straight linear interpolation. A take is played to listen to,
not to measure: the analysis path never sees these samples, and the device
rate differs from the file's only when the two were chosen by different
machines.
"""

from __future__ import annotations

import numpy as np
import soundfile as sf

from tuner.audio.output import AudioOutput


def resampled(signal: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """`signal` at `to_sr`, same duration. Linear, which is enough to listen to."""
    if from_sr == to_sr or len(signal) == 0 or not from_sr or not to_sr:
        return np.asarray(signal, dtype=np.float64)
    count = round(len(signal) * to_sr / from_sr)
    if count <= 0:
        return np.zeros(0, dtype=np.float64)
    source = np.arange(len(signal), dtype=np.float64)
    wanted = np.linspace(0.0, len(signal) - 1, count)
    return np.interp(wanted, source, np.asarray(signal, dtype=np.float64))


class TakePlayer:
    """One take at a time. Starting another replaces the one playing."""

    def __init__(self, output: AudioOutput):
        self._output = output
        self._signal = np.zeros(0)
        self._position = 0
        self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> int:
        return self._position

    def play_signal(self, signal: np.ndarray, sr: int) -> None:
        was_playing = self._playing
        self._signal = resampled(signal, sr, self._output.sample_rate)
        self._position = 0
        self._playing = True
        if not was_playing:
            self._output.start(self._render)

    def play_file(self, path) -> float:
        """Read and play a take; returns its length in seconds."""
        signal, sr = sf.read(str(path), always_2d=True)
        mono = signal.mean(axis=1)
        self.play_signal(mono, int(sr))
        return len(mono) / float(sr) if sr else 0.0

    def stop(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._signal = np.zeros(0)
        self._position = 0
        self._output.stop()

    def _render(self, frames: int) -> np.ndarray:
        """Pulled on the audio thread. Past the end it returns silence rather
        than closing the stream: tearing a device down from its own callback is
        how PortAudio deadlocks, so the window stops it instead."""
        if not self._playing:
            return np.zeros(frames)
        chunk = self._signal[self._position : self._position + frames]
        self._position += frames
        if len(chunk) < frames:
            chunk = np.concatenate([chunk, np.zeros(frames - len(chunk))])
        return chunk

    @property
    def finished(self) -> bool:
        return self._playing and self._position >= len(self._signal)
