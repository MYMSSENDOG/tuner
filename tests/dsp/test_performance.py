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


def test_metronome_render_fits_the_output_callback():
    """The metronome renders inside the device's callback, so one block has
    to be produced in far less than the block's own duration — an overrun
    there is a dropout in the click, which is the one thing a metronome may
    never do."""
    from tuner.audio.sounddevice_output import BLOCK_SIZE
    from tuner.core.metronome import Metronome

    metronome = Metronome(SR, 208.0)  # fastest tempo = most clicks per block
    metronome.render(BLOCK_SIZE)  # warm-up

    rounds = 2000
    start = time.perf_counter()
    for _ in range(rounds):
        metronome.render(BLOCK_SIZE)
    per_call = (time.perf_counter() - start) / rounds

    budget = BLOCK_SIZE / SR
    print(f"\nmetronome: {per_call * 1000:.3f}ms per block (budget {budget * 1000:.1f}ms)")
    record("perf/metronome_render_ms", per_call * 1000.0, unit="ms")
    assert per_call < budget / 4


def test_click_suppression_fits_the_audio_callback():
    """Suppression adds an FFT to every input block — 172 of them a second,
    on the audio thread, on top of the detector. It has to be noise in that
    budget, not a second cost of the same order."""

    from tuner.core.interference import HeardClicks

    source = HeardClicks()
    source.set_period(0.5)
    block = tone(440.0, 1.0, instrument="violin")[:256]
    source.observe(block, 0.0, SR)  # warm-up

    rounds = 5000
    start = time.perf_counter()
    for i in range(rounds):
        source.observe(block, i * 256 / SR, SR)
    per_call = (time.perf_counter() - start) / rounds

    budget = 256 / SR
    print(f"\nclick suppression: {per_call * 1000:.3f}ms per block "
          f"(budget {budget * 1000:.1f}ms)")
    record("perf/click_suppression_ms", per_call * 1000.0, unit="ms")
    assert per_call < budget / 10
