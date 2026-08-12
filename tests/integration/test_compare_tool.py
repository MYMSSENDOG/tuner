"""Variant-comparison harness: several display policies over the same audio."""

import pytest

pytest.importorskip("PySide6")

from tests.fakes import FakeAudioInput
from tests.synth import tone
from tuner.core.tracker import State
from tuner.tools.compare import (
    DEFAULT_VARIANTS,
    Variant,
    build_compare_window,
    parse_variant,
)


def test_panes_run_their_own_variant(qapp):
    signal = tone(440.0, 0.3, instrument="violin")
    fakes = [FakeAudioInput(signal) for _ in DEFAULT_VARIANTS]
    view = build_compare_window(fakes, DEFAULT_VARIANTS)

    for engine in view.engines:
        engine.start()
    for fake in fakes:
        fake.pump()
    qapp.processEvents()

    # every pane received readings from sample-identical audio
    for meter, variant in zip(view.meters, DEFAULT_VARIANTS):
        reading = meter._reading
        assert reading is not None and reading.state is State.OK, variant.label
        assert reading.note.label == "A4"

    # and each engine really runs its own smoothing policy
    smoothers = [engine._tracker._smoother for engine in view.engines]
    assert smoothers[0] is None  # raw variant
    assert all(s is not None for s in smoothers[1:])
    params = {(s._min_cutoff, s._beta) for s in smoothers[1:]}
    assert len(params) == len(smoothers) - 1  # all combinations distinct


def test_default_grid_is_eight_panes():
    from tuner.tools.compare import MAX_PANES

    assert len(DEFAULT_VARIANTS) == MAX_PANES == 8
    assert all(v.desc for v in DEFAULT_VARIANTS)  # every pane explains itself


def test_engine_accepts_tracker_factory():
    from tuner.app.engine import TunerEngine
    from tuner.core.tracker import PitchTracker

    made = []

    def factory(dt):
        tracker = PitchTracker(smooth_min_cutoff_hz=None, dt_s=dt)
        made.append(tracker)
        return tracker

    engine = TunerEngine(FakeAudioInput(), lambda r: None, tracker_factory=factory)
    assert engine._tracker is made[-1]


def test_parse_variant():
    v = parse_variant("강함:0.5:0.02")
    assert v == Variant("강함", 0.5, 0.02)
    assert parse_variant("raw:off").min_cutoff_hz is None
    with_desc = parse_variant("실험:0.7:0.03:내 설명")
    assert with_desc.desc == "내 설명" and with_desc.beta == 0.03
