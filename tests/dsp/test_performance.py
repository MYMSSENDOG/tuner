"""Throughput regression gates, anchored to real time.

A tuner that cannot keep up with real time is broken as a product, and an
annotator that grinds makes the fixture workflow unusable — this repo has
already had one pathological slowdown (an infinite loop in the annotator
that turned a 22s job into 10+ CPU-minutes). Plain correctness tests never
notice an order-of-magnitude slowdown; these do.

Bounds are deliberately loose (5-8x the measured local factor) so slow CI
machines don't flake — the target is regressions of magnitude, not of
percent. Measured locally: pipeline 0.14x realtime, annotator 0.67x.
"""

import time

import pytest

from tests.helpers import track_signal
from tests.metrics import record
from tests.synth import SR, tone
from tuner.analysis.reference import annotate

# Wall-clock measurements: meaningless when 12 xdist workers share the CPU,
# so conftest skips them there. Run with `pytest -m perf -n0`.
pytestmark = pytest.mark.perf

CLIP_SECONDS = 2.0


def _realtime_factor(fn) -> float:
    signal = tone(440.0, CLIP_SECONDS, instrument="violin")
    start = time.perf_counter()
    fn(signal)
    return (time.perf_counter() - start) / CLIP_SECONDS


def test_realtime_pipeline_keeps_up():
    """The full production path (detector + tracker) must stay well under
    1x realtime — at 1x the display starts lagging the sound."""
    factor = _realtime_factor(lambda s: track_signal(s, SR))
    print(f"\npipeline: {factor:.3f}x realtime (local baseline 0.14)")
    record("perf/pipeline_realtime_factor", factor)
    assert factor < 1.0


def test_annotator_stays_practical():
    factor = _realtime_factor(lambda s: annotate(s, SR))
    print(f"\nannotator: {factor:.3f}x realtime (local baseline 0.67)")
    record("perf/annotator_realtime_factor", factor)
    assert factor < 5.0
