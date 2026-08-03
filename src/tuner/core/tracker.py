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


class PitchTracker:
    def __init__(
        self,
        min_confidence: float = 0.5,
        jump_cents: float = 80.0,
        confirm_frames: int = 2,
        hold_frames: int = 6,
    ):
        self._min_confidence = min_confidence
        self._jump_cents = jump_cents
        self._confirm_frames = confirm_frames
        self._hold_frames = hold_frames
        self._freq: float | None = None
        self._candidate: float | None = None
        self._candidate_count = 0
        self._dropout = 0

    def update(self, result: PitchResult) -> TrackedPitch:
        voiced = result.freq_hz is not None and result.confidence >= self._min_confidence
        if not voiced:
            self._dropout += 1
            if self._freq is not None and self._dropout <= self._hold_frames:
                return TrackedPitch(self._freq, State.OK)
            self._freq = None
            self._candidate = None
            state = State.SILENT if result.freq_hz is None else State.NOISY
            return TrackedPitch(None, state)

        self._dropout = 0
        freq = result.freq_hz
        assert freq is not None

        if self._freq is None or _cents_between(freq, self._freq) < self._jump_cents:
            self._freq = freq
            self._candidate = None
        else:
            # large jump: require consecutive agreeing frames before following
            if self._candidate is not None and _cents_between(freq, self._candidate) < 50.0:
                self._candidate_count += 1
            else:
                self._candidate = freq
                self._candidate_count = 1
            if self._candidate_count >= self._confirm_frames:
                self._freq = freq
                self._candidate = None
        return TrackedPitch(self._freq, State.OK)
