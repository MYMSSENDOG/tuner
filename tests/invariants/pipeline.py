"""Driving the real display pipeline, for the walls that compare two runs.

tools/trace.py already runs the engine offline, but it fixes the block size
and the A4 reference — the two things these invariants need to vary. This is
the same assembly with those knobs exposed, and nothing else: the detector,
tracker and latch are the app's own.
"""

from __future__ import annotations

import numpy as np

from tests.fakes import FakeAudioInput
from tests.synth import SR
from tuner.app.engine import TunerEngine, TunerReading
from tuner.core.tracker import State

BLOCK = 256


def readings(
    signal: np.ndarray,
    sr: int = SR,
    *,
    block_size: int = BLOCK,
    a4_hz: float = 440.0,
) -> list[TunerReading]:
    """Every reading the app would emit for this signal, in order."""
    collected: list[TunerReading] = []
    audio = FakeAudioInput(np.asarray(signal, dtype=float), block_size=block_size, sr=sr)
    engine = TunerEngine(audio, collected.append)
    engine.set_a4(a4_hz)
    engine.start()
    audio.pump()
    engine.stop()
    return collected


def displayed_hz(readings_: list[TunerReading]) -> list[float | None]:
    return [r.note.freq_hz if r.note is not None else None for r in readings_]


def voiced(readings_: list[TunerReading]) -> list[TunerReading]:
    return [r for r in readings_ if r.state is State.OK and r.note is not None]
