"""Every display state must actually paint without raising.

Correctness of the painted pixels is a human's call, but a crash or an
unhandled state in a paintEvent only surfaces at runtime — these force each
branch (in-tune / orange / red / ghost / NOISY / silent, traces with gaps
and labels, level bar around the gate) through a real offscreen render.
"""

import pytest

pytest.importorskip("PySide6")

from tuner.app.engine import TunerReading
from tuner.app.level_widget import GATE_DBFS, InputLevelBar
from tuner.app.meter_widget import MeterWidget
from tuner.app.trace_widget import PitchTraceWidget
from tuner.core.notes import freq_to_note
from tuner.core.tracker import State

A4 = 440.0


def ok(cents: float) -> TunerReading:
    return TunerReading(state=State.OK, note=freq_to_note(A4 * 2 ** (cents / 1200)))


def render(widget) -> None:
    widget.resize(320, 320)
    image = widget.grab().toImage()
    assert not image.isNull() and image.width() > 0


METER_STATES = {
    "empty": [],
    "green": [ok(0.0)],
    "orange": [ok(12.0)],
    "red": [ok(30.0)],
    "clamped": [ok(65.0)],  # latch holding past the scale end
    "noisy": [ok(0.0), TunerReading(state=State.NOISY, note=None)],
    "ghost": [ok(0.0), TunerReading(state=State.SILENT, note=None)],
}


@pytest.mark.parametrize("name,readings", METER_STATES.items(), ids=list(METER_STATES))
def test_meter_paints_every_state(qapp, name, readings):
    meter = MeterWidget()
    for reading in readings:
        meter.set_reading(reading)
    render(meter)


def test_trace_paints_gaps_and_labels(qapp):
    trace = PitchTraceWidget()
    for cents in range(40):
        trace.add_reading(ok(float(cents)))
    trace.add_reading(TunerReading(state=State.SILENT, note=None))
    for _ in range(20):
        trace.add_reading(ok(-600.0))  # new note after a gap -> boundary label
    render(trace)


def test_level_bar_paints_around_gate(qapp):
    bar = InputLevelBar()
    for level in (-120.0, GATE_DBFS - 5, GATE_DBFS + 5, 0.0):
        bar.set_level(level)
        render(bar)
