"""Time-axis display policy on top of raw per-frame pitch detection.

The detector stays maximally sensitive (raw per-frame output); this tracker
decides what the meter should show:

- low-confidence / unvoiced frames never move the display,
- brief dropouts hold the last pitch instead of flickering,
- small changes pass through immediately (glissando/vibrato tracking),
- large jumps need two consistent frames (rejects 1-frame octave glitches).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from tuner.core.pitch import PitchResult


class State(Enum):
    OK = "ok"
    NOISY = "noisy"
    SILENT = "silent"


@dataclass(frozen=True)
class TrackedPitch:
    freq_hz: float | None
    state: State


def _cents_between(f1: float, f2: float) -> float:
    return abs(1200.0 * math.log2(f1 / f2))


class _OneEuro:
    """One-Euro filter (Casiez et al.): smoothing whose cutoff opens with the
    signal's speed — steady values get heavy smoothing (still needle), fast
    changes pass nearly untouched (glissando/vibrato tracking).

    Operates in cents (log-frequency), the musically uniform domain.
    """

    def __init__(self, dt_s: float, min_cutoff_hz: float, beta: float, d_cutoff_hz: float = 1.0):
        self._dt = dt_s
        self._min_cutoff = min_cutoff_hz
        self._beta = beta
        self._d_cutoff = d_cutoff_hz
        self._x: float | None = None
        self._dx = 0.0

    def _alpha(self, cutoff_hz: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff_hz)
        return 1.0 / (1.0 + tau / self._dt)

    def reset(self, x: float) -> None:
        self._x = x
        self._dx = 0.0

    def update(self, x: float) -> float:
        if self._x is None:
            self.reset(x)
            return x
        dx = (x - self._x) / self._dt
        a_d = self._alpha(self._d_cutoff)
        self._dx += a_d * (dx - self._dx)
        cutoff = self._min_cutoff + self._beta * abs(self._dx)
        a = self._alpha(cutoff)
        self._x += a * (x - self._x)
        return self._x


class PitchTracker:
    def __init__(
        self,
        min_confidence: float = 0.5,
        jump_cents: float = 80.0,
        confirm_frames: int = 2,
        hold_frames: int = 6,
        smooth_min_cutoff_hz: float | None = 1.0,
        smooth_beta: float = 0.04,
        dt_s: float = 256.0 / 44100.0,
    ):
        # smoothing defaults are the knee of the measured jitter/vibrato
        # trade-off (docs/smoothing-tuning.md): jitter p95 0.94 -> 0.52 cents
        # while keeping 85% of vibrato amplitude and note-change response
        # untouched (confirmed jumps bypass the filter entirely).
        self._min_confidence = min_confidence
        self._jump_cents = jump_cents
        self._confirm_frames = confirm_frames
        self._hold_frames = hold_frames
        self._smoother = (
            _OneEuro(dt_s, smooth_min_cutoff_hz, smooth_beta)
            if smooth_min_cutoff_hz is not None
            else None
        )
        self._freq: float | None = None
        self._candidate: float | None = None
        self._candidate_count = 0
        self._dropout = 0

    def _display(self, freq: float, jump: bool) -> float:
        """Smooth small motion; a confirmed jump is a new note — snap to it."""
        if self._smoother is None:
            return freq
        cents = 1200.0 * math.log2(freq / 440.0)
        if jump:
            self._smoother.reset(cents)
            return freq
        return 440.0 * 2.0 ** (self._smoother.update(cents) / 1200.0)

    def update(self, result: PitchResult) -> TrackedPitch:
        voiced = result.freq_hz is not None and result.confidence >= self._min_confidence
        if not voiced:
            self._dropout += 1
            if self._freq is not None and self._dropout <= self._hold_frames:
                return TrackedPitch(self._freq, State.OK)
            self._freq = None
            self._candidate = None
            if self._smoother is not None:
                self._smoother._x = None
            state = State.SILENT if result.freq_hz is None else State.NOISY
            return TrackedPitch(None, state)

        self._dropout = 0
        freq = result.freq_hz
        assert freq is not None

        if self._freq is None or _cents_between(freq, self._freq) < self._jump_cents:
            self._freq = self._display(freq, jump=self._freq is None)
            self._candidate = None
        else:
            # large jump: require consecutive agreeing frames before following
            if self._candidate is not None and _cents_between(freq, self._candidate) < 50.0:
                self._candidate_count += 1
            else:
                self._candidate = freq
                self._candidate_count = 1
            if self._candidate_count >= self._confirm_frames:
                self._freq = self._display(freq, jump=True)
                self._candidate = None
        return TrackedPitch(self._freq, State.OK)
