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
from tuner.core.metronome import (
    CLICK_AMPLITUDE,
    CLICK_SOUNDS,
    DEFAULT_BPM,
    DEFAULT_SOUND,
    Metronome,
    clamp_bpm,
    clamp_volume,
    sound_waveform,
)


class MetronomeService:
    """Start/stop and tempo, plus the click timeline the tuner reads."""

    def __init__(
        self,
        output: AudioOutput,
        bpm: float = DEFAULT_BPM,
        volume: float = CLICK_AMPLITUDE,
        sound: str = DEFAULT_SOUND,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._output = output
        self._clock = clock
        self._bpm = clamp_bpm(bpm)
        self._volume = clamp_volume(volume)
        self._sound = sound if sound in CLICK_SOUNDS else DEFAULT_SOUND
        self._metronome: Metronome | None = None
        self._running = False
        # handed to the tuner engine once, at construction. It is told the
        # tempo and nothing else - where the beat actually falls it works out
        # from the microphone, which is why a wrong latency figure or a
        # drifting output clock cannot aim it wrongly (docs/metronome.md).
        self.clicks = HeardClicks()
        # a one-shot renderer, live only while the sound picker is auditioning
        # something with the beat stopped
        self._preview: np.ndarray | None = None
        self._preview_at = 0

    @property
    def bpm(self) -> float:
        return self._bpm

    @property
    def running(self) -> bool:
        return self._running

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def sound(self) -> str:
        return self._sound

    def set_sound(self, name: str) -> str:
        """Change what a beat sounds like, mid-bar. Returns the name taken —
        an unknown one falls back to the default rather than going silent."""
        self._sound = name if name in CLICK_SOUNDS else DEFAULT_SOUND
        self._publish_sound_length()
        if self._metronome is not None:
            self._metronome.set_sound(self._sound)
        return self._sound

    def preview(self, name: str) -> None:
        """Let the sound be heard, which is the only way anyone picks one.

        Running, that is the next beat and nothing else is needed. Stopped, it
        is one click through the device, which the caller ends with
        end_preview() once it has been heard.
        """
        self.set_sound(name)
        if self._running:
            return
        # clicking down a list auditions several in a row; that is one stream
        # with the buffer swapped under it, not one stream per row
        open_already = self._preview is not None
        self._preview = sound_waveform(
            self._sound, self._output.sample_rate, self._volume
        )
        self._preview_at = 0
        if not open_already:
            self._output.start(self._render)

    def end_preview(self) -> None:
        """Close the device the preview opened. A no-op while the beat runs,
        which owns the device itself."""
        if self._preview is not None and not self._running:
            self._output.stop()
        self._preview = None

    @property
    def previewing(self) -> bool:
        return self._preview is not None and not self._running

    def _publish_sound_length(self) -> None:
        """The suppressor is told how long our sound lasts as well as how
        often — both are things we know because we are the one playing it."""
        self.clicks.set_sound_length(
            len(sound_waveform(self._sound, self._output.sample_rate))
            / self._output.sample_rate
        )

    def set_volume(self, volume: float) -> float:
        """How loud the click is. Live, and it needs no restart — turning it
        down while it plays is the only way to find the right level.

        Turning it to nothing also turns off click suppression, without
        anything here saying so: the tuner finds the beat by listening
        (core/interference.py), and a click the microphone cannot hear is one
        it will not freeze the display for.
        """
        self._volume = clamp_volume(volume)
        if self._metronome is not None:
            self._metronome.set_volume(self._volume)
        return self._volume

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
        self._preview = None  # the beat takes the device over from any audition
        # built for the rate the device will actually open at, which is why
        # AudioOutput can be asked for it before the stream exists
        self._metronome = Metronome(
            self._output.sample_rate, self._bpm, self._volume, self._sound
        )
        self._running = True
        self.clicks.set_period(60.0 / self._bpm)
        self._publish_sound_length()
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
            return self._render_preview(frames)
        return metronome.render(frames)

    def _render_preview(self, frames: int) -> np.ndarray:
        out = np.zeros(frames)
        sound = self._preview
        if sound is None:
            return out
        take = max(min(len(sound) - self._preview_at, frames), 0)
        if take:
            out[:take] = sound[self._preview_at : self._preview_at + take]
        self._preview_at += frames
        return out
