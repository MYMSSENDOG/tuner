"""Dragging the level bar moves the detection gate.

The gate used to be a constant with a product rule saying no UI would expose
it. A room overrode that: a field recording had a tonal hum sitting just above
-40dBFS, so the studio-measured number was open on nothing but the room
(docs/pitch-pipeline.md). These tests hold the control to what that implies —
the number is visible, it moves, it reaches the detector, and it survives a
restart.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtGui import QEnterEvent, QMouseEvent, QPointingDevice

from tests.fakes import FakeAudioInput, FakeAudioOutput
from tests.synth import tone
from tuner.app.level_widget import (
    BAR_H,
    HANDLE_OVERHANG,
    RANGE_DBFS,
    InputLevelBar,
    gate_from_x,
)

WIDTH = 300


# --- the arithmetic, without Qt ------------------------------------------


def test_gate_from_x_spans_the_bar():
    lo, hi = RANGE_DBFS
    assert gate_from_x(0, WIDTH) == lo
    assert gate_from_x(WIDTH - 1, WIDTH) == hi


def test_gate_from_x_is_clamped_to_the_bar():
    """A drag that leaves the widget still reports a usable gate."""
    lo, hi = RANGE_DBFS
    assert gate_from_x(-50, WIDTH) == lo
    assert gate_from_x(WIDTH * 3, WIDTH) == hi


def test_gate_from_x_snaps_to_whole_decibels():
    """290px over 60dB is 0.2dB a pixel; finer steps would be a value nobody
    chose and a readout that never settles."""
    values = {gate_from_x(x, WIDTH) for x in range(WIDTH)}
    assert all(v == round(v) for v in values)
    assert len(values) == int(RANGE_DBFS[1] - RANGE_DBFS[0]) + 1


def test_gate_from_x_round_trips_through_fill_fraction():
    from tuner.app.level_widget import fill_fraction

    for x in range(0, WIDTH, 7):
        gate = gate_from_x(x, WIDTH)
        assert abs(fill_fraction(gate) * WIDTH - x) <= 3  # within a snap step


# --- the widget ----------------------------------------------------------


def drag_to(bar: InputLevelBar, x: int) -> None:
    position = QPointF(x, bar.height() / 2)
    for kind in ("press", "move"):
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress if kind == "press" else QMouseEvent.Type.MouseMove,
            position,
            position,  # global; offscreen, so the same point
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPointingDevice.primaryPointingDevice(),
        )
        if kind == "press":
            bar.mousePressEvent(event)
        else:
            bar.mouseMoveEvent(event)


def hover(bar: InputLevelBar) -> None:
    point = QPointF(bar.width() / 2, bar.height() / 2)
    bar.enterEvent(QEnterEvent(point, point, point))


def test_dragging_reports_the_new_gate(qapp):
    bar = InputLevelBar()
    bar.resize(WIDTH, BAR_H + HANDLE_OVERHANG)
    seen: list[float] = []
    bar.gate_changed.connect(seen.append)

    drag_to(bar, 0)
    drag_to(bar, WIDTH - 1)
    assert seen == [RANGE_DBFS[0], RANGE_DBFS[1]]
    assert bar.gate_dbfs == RANGE_DBFS[1]


def test_a_drag_that_changes_nothing_is_silent(qapp):
    """Moving within one snap step must not spray signals at the engine."""
    bar = InputLevelBar()
    bar.resize(WIDTH, BAR_H + HANDLE_OVERHANG)
    drag_to(bar, 100)
    seen: list[float] = []
    bar.gate_changed.connect(seen.append)
    drag_to(bar, 101)
    assert seen == []


def test_set_gate_does_not_echo_back(qapp):
    """Restored settings feed the bar; if that emitted, the value would take a
    lap through the engine and back on every startup."""
    bar = InputLevelBar()
    seen: list[float] = []
    bar.gate_changed.connect(seen.append)
    bar.set_gate(-33.0)
    assert bar.gate_dbfs == -33.0
    assert seen == []


def test_hovering_shows_the_number_over_the_meter(qapp):
    """'몇인지 볼 수 있게' — and drawn on top rather than in the 8px bar, so
    the meter keeps its pixels when nobody is looking."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    host = QWidget()
    host.resize(WIDTH, 120)
    layout = QVBoxLayout(host)
    bar = InputLevelBar()
    layout.addWidget(bar)
    host.show()
    bar.resize(WIDTH, BAR_H + HANDLE_OVERHANG)
    bar.set_gate(-40.0)

    assert bar._readout.label is None  # nothing exists until hovered
    hover(bar)
    readout = bar._readout.label
    assert readout is not None and readout.isVisible()
    assert readout.text() == "-40 dB"
    assert readout.parentWidget() is host  # overlaid on the window, not clipped
    # the widget is the bar plus the handle's overhang, and no more: text is
    # what the overlay is for, so hovering must not resize anything
    assert bar.height() == BAR_H + HANDLE_OVERHANG

    bar.leaveEvent(None)
    assert not readout.isVisible()
    host.close()


def test_the_readout_follows_a_drag(qapp):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    host = QWidget()
    host.resize(WIDTH, 120)
    QVBoxLayout(host).addWidget(bar := InputLevelBar())
    host.show()
    bar.resize(WIDTH, BAR_H + HANDLE_OVERHANG)

    hover(bar)
    drag_to(bar, 0)
    assert bar._readout.label.text() == f"{RANGE_DBFS[0]:.0f} dB"
    drag_to(bar, WIDTH - 1)
    assert bar._readout.label.text() == f"{RANGE_DBFS[1]:.0f} dB"
    host.close()


# --- wired into the window ------------------------------------------------


@pytest.fixture
def make_window(qapp, tmp_path):
    from tuner.app.main_window import MainWindow

    def factory():
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        return MainWindow(
            FakeAudioInput(tone(440.0, 0.05)), settings, FakeAudioOutput()
        )

    return factory


def test_the_bar_starts_showing_the_engine_gate(make_window):
    window = make_window()
    assert window._level_bar.gate_dbfs == pytest.approx(window._engine.input_gate_dbfs)
    assert window._level_bar.gate_dbfs == pytest.approx(-40.0)
    window.close()


def test_dragging_reaches_the_detector(make_window):
    """The whole chain in one assertion: bar -> engine -> the detector that
    the audio thread is about to ask."""
    window = make_window()
    window._level_bar.resize(WIDTH, BAR_H + HANDLE_OVERHANG)
    drag_to(window._level_bar, 0)

    assert window._engine.input_gate_dbfs == pytest.approx(RANGE_DBFS[0])
    assert window._engine._detector.input_gate_rms == pytest.approx(
        10.0 ** (RANGE_DBFS[0] / 20.0)
    )
    window.close()


def test_the_gate_survives_a_restart(make_window):
    window = make_window()
    window._level_bar.resize(WIDTH, BAR_H + HANDLE_OVERHANG)
    drag_to(window._level_bar, 30)
    chosen = window._engine.input_gate_dbfs
    window.close()

    restored = make_window()
    assert restored._engine.input_gate_dbfs == pytest.approx(chosen)
    assert restored._level_bar.gate_dbfs == pytest.approx(chosen)
    restored.close()
