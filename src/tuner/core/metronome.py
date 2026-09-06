"""Metronome timing: where the clicks are, and what one sounds like.

Pure arithmetic on a sample clock — no Qt, no audio device, no wall clock.
That is the whole point: a metronome is only worth anything if beat k lands
exactly where arithmetic says it should, and arithmetic is something tests
can check to the sample.

Two properties this file exists to guarantee, both sealed in
tests/unit/test_metronome.py:

- **no drift**: beat k is computed from k (`origin + round(k * period)`),
  never by adding a period to the previous beat. Accumulating float error
  at 137 BPM would be inaudible for a minute and obvious after ten.
- **block-size independence**: rendering the same span in blocks of 64 or
  1024 produces identical samples, because a click is mixed from wherever it
  starts rather than carried across calls in a buffer. The audio device is
  free to choose whatever block size it likes.
"""

from __future__ import annotations

import math

import numpy as np

# Bounds on the tempo the UI may ask for. 30 is slower than any tempo marking
# in use (Larghissimo sits around 40); 300 is past Prestissimo. Wider than
# anyone needs on purpose - the point of the bound is to keep a typo out of
# the audio callback, not to have an opinion about music.
MIN_BPM = 30.0
MAX_BPM = 300.0
DEFAULT_BPM = 120.0

# The click itself: a short decaying sine burst, synthesised rather than
# loaded from a file. It has to be broadband enough to hear over an
# instrument and short enough not to smear the beat it marks; 20ms is under
# a thirtieth of the fastest beat this allows (300 BPM = 200ms).
CLICK_HZ = 1000.0
CLICK_MS = 20.0
CLICK_DECAY = 25.0  # e-folds per second of the amplitude envelope
# Volume *is* the click's peak amplitude, so the control has an absolute
# meaning rather than a multiple of some hidden reference: 0 is silent, 1 is
# full scale, and the default sits in the middle with room either way.
CLICK_AMPLITUDE = 0.5
MIN_VOLUME = 0.0
MAX_VOLUME = 1.0


def clamp_volume(volume: float) -> float:
    return min(max(float(volume), MIN_VOLUME), MAX_VOLUME)


def click_waveform(
    sr: int,
    freq_hz: float = CLICK_HZ,
    ms: float = CLICK_MS,
    amplitude: float = CLICK_AMPLITUDE,
) -> np.ndarray:
    """One click, as samples. Starts and ends at zero, so mixing it anywhere
    into a stream cannot produce a discontinuity of its own."""
    n = max(round(sr * ms / 1000.0), 1)
    t = np.arange(n) / sr
    envelope = np.exp(-CLICK_DECAY * t)
    # taper the last samples to zero: the exponential is still ~0.6 at 20ms
    fade = min(n, max(round(sr * 0.004), 1))
    envelope[-fade:] *= np.linspace(1.0, 0.0, fade)
    return (amplitude * envelope * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.float64)


def clamp_bpm(bpm: float) -> float:
    return min(max(float(bpm), MIN_BPM), MAX_BPM)


class ClickSchedule:
    """Which sample each beat starts on.

    Beat k is at `origin + round(k * period)`. Changing the tempo rebases the
    schedule onto the beat that has most recently sounded, so the beat you
    are hearing keeps its place and only the *next* interval takes the new
    tempo — the alternative (recomputing from sample 0) makes the beat jump
    sideways the instant you touch the dial.
    """

    def __init__(self, bpm: float, sr: int, origin: int = 0):
        self._sr = sr
        self._origin = origin
        self._bpm = clamp_bpm(bpm)

    @property
    def bpm(self) -> float:
        return self._bpm

    @property
    def period_samples(self) -> float:
        return 60.0 * self._sr / self._bpm

    def beat_sample(self, k: int) -> int:
        """Absolute sample index of beat k. From k, never accumulated."""
        return self._origin + round(k * self.period_samples)

    def rebase(self, bpm: float, at_sample: int) -> None:
        """Change tempo without moving the beat currently in progress."""
        self._origin = self.beat_sample(self.beat_before(at_sample))
        self._bpm = clamp_bpm(bpm)

    def beat_before(self, sample: int) -> int:
        """Index of the last beat at or before `sample` (may be negative)."""
        return math.floor((sample - self._origin) / self.period_samples)

    def beats_touching(self, start: int, count: int, span: int) -> list[int]:
        """Beats whose `span`-sample sound overlaps [start, start+count).

        Includes beats that began before this window — a click straddling a
        block boundary is the normal case, not an edge case.
        """
        if count <= 0:
            return []
        first = max(self.beat_before(start - span + 1), 0)
        last = self.beat_before(start + count - 1)
        return [
            k
            for k in range(first, last + 1)
            if start - span < self.beat_sample(k) < start + count
        ]


class Metronome:
    """Renders a ClickSchedule into successive blocks of output.

    The only state is the sample position, so the audio device may hand it
    any block size and get bit-identical audio (test_block_size_invariance).
    """

    def __init__(
        self,
        sr: int,
        bpm: float = DEFAULT_BPM,
        volume: float = CLICK_AMPLITUDE,
    ):
        self._sr = sr
        # kept at full scale and scaled at render: volume is read on the audio
        # thread and written from the UI, and a float is a safer thing to swap
        # under it than an array it is halfway through reading
        self._click = click_waveform(sr, amplitude=1.0)
        self._volume = clamp_volume(volume)
        self._schedule = ClickSchedule(bpm, sr)
        self._position = 0

    @property
    def sr(self) -> int:
        return self._sr

    @property
    def bpm(self) -> float:
        return self._schedule.bpm

    @property
    def position(self) -> int:
        """Samples rendered since reset(); the metronome's own clock."""
        return self._position

    @property
    def volume(self) -> float:
        return self._volume

    def set_volume(self, volume: float) -> None:
        """Peak amplitude of the click, 0 (silent) to 1 (full scale)."""
        self._volume = clamp_volume(volume)

    def set_bpm(self, bpm: float) -> None:
        self._schedule.rebase(bpm, self._position)

    def reset(self) -> None:
        """Back to a beat at sample 0 — a fresh start, not a resume."""
        self._schedule = ClickSchedule(self._schedule.bpm, self._sr)
        self._position = 0

    def beat_samples_in(self, start: int, count: int) -> list[int]:
        """Where the beats in this span begin. What the click suppressor needs
        to know, and what the timing tests measure against."""
        return [
            self._schedule.beat_sample(k)
            for k in self._schedule.beats_touching(start, count, span=1)
        ]

    def render(self, frames: int) -> np.ndarray:
        """The next `frames` samples of output, advancing the clock."""
        out = np.zeros(frames)
        start, span = self._position, len(self._click)
        for k in self._schedule.beats_touching(start, frames, span):
            at = self._schedule.beat_sample(k) - start
            lo, hi = max(at, 0), min(at + span, frames)
            out[lo:hi] += self._click[lo - at : hi - at] * self._volume
        self._position += frames
        return out
