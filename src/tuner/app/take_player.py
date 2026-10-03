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
    """One take at a time, with a position the view can show and move.

    The device keeps pulling while paused — it is handed silence rather than
    being stopped — because opening and closing a stream for every pause is
    both slow and the shape of an audio-thread deadlock. `loaded` is therefore
    the state that matters to a view: there is a take in hand, playing or not.
    """

    def __init__(self, output: AudioOutput):
        self._output = output
        self._signal = np.zeros(0)
        self._position = 0
        self._loaded = False
        self._playing = False
        self._path = None

    # --- what the view reads ---------------------------------------------

    @property
    def playing(self) -> bool:
        """Sound is coming out right now."""
        return self._playing

    @property
    def loaded(self) -> bool:
        """A take is in hand — playing, paused, or sitting at its end."""
        return self._loaded

    @property
    def path(self):
        return self._path

    @property
    def position(self) -> int:
        return min(self._position, len(self._signal))

    @property
    def sample_rate(self) -> int:
        return self._output.sample_rate or 1

    @property
    def position_seconds(self) -> float:
        return self.position / self.sample_rate

    @property
    def duration_seconds(self) -> float:
        return len(self._signal) / self.sample_rate

    @property
    def finished(self) -> bool:
        return self._loaded and self._position >= len(self._signal)

    # --- what the window does ---------------------------------------------

    def load_file(self, path) -> float:
        """Read a take and hold it at position zero, without starting it."""
        signal, sr = sf.read(str(path), always_2d=True)
        mono = signal.mean(axis=1)
        self._signal = resampled(mono, int(sr), self._output.sample_rate)
        self._position = 0
        self._path = path
        self._loaded = True
        self._playing = False
        return self.duration_seconds

    def play(self) -> None:
        if not self._loaded:
            return
        if self.finished:
            self._position = 0  # pressing play at the end starts it over
        started = self._playing
        self._playing = True
        if not started:
            self._output.start(self._render)

    def pause(self) -> None:
        self._playing = False

    def toggle(self) -> None:
        self.pause() if self._playing else self.play()

    def seek_seconds(self, seconds: float) -> None:
        self._position = int(max(0.0, seconds) * self.sample_rate)

    def stop(self) -> None:
        """Give the device back and forget the take."""
        was_open = self._loaded
        self._loaded = False
        self._playing = False
        self._signal = np.zeros(0)
        self._position = 0
        self._path = None
        if was_open:
            self._output.stop()

    def _render(self, frames: int) -> np.ndarray:
        """Pulled on the audio thread. Paused or past the end it returns
        silence rather than closing the stream: tearing a device down from its
        own callback is how PortAudio deadlocks, so the window stops it."""
        if not self._playing:
            return np.zeros(frames)
        chunk = self._signal[self._position : self._position + frames]
        self._position += frames
        if len(chunk) < frames:
            chunk = np.concatenate([chunk, np.zeros(frames - len(chunk))])
            self._playing = False  # reached the end; the device stays open
        return chunk
