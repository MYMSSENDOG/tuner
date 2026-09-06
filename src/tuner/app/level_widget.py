"""Thin input-level bar with the detection gate marked — and draggable.

Answers "is sound reaching the tuner at all?" at a glance: the fill shows
the microphone level, the tick shows the gate below which the detector
refuses to judge — so "signal present but too quiet" and "no signal" look
different, instead of both being a blank meter.

The gate is also the control, and it carries a handle so that reads as true:
a 1px tick says "here is the threshold", a grip says "take hold of this". One
studio-measured gate cannot fit every room (docs/pitch-pipeline.md), so the
handle can be dragged and hovering shows where it currently sits.

Two things are deliberately kept out of the bar's own 8px. The handle's grip
sticks up 3px above it — upward only, because down is the window edge and
because every pixel spent here is one the meter loses. The readout goes
further: it is a label parented to the window, overlapping the meter only
while the pointer is on the bar, since reserving room for text all session to
serve a number wanted for a second is a bad trade.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QWidget

from tuner.core.detector import INPUT_GATE_RMS

RANGE_DBFS = (-60.0, 0.0)
BAR_H = 8  # the level bar itself
HANDLE_OVERHANG = 3  # how far the grip rises above it
HANDLE_W = 9  # wide enough to take hold of; the middle groove is the value
DEFAULT_GATE_DBFS = 20.0 * math.log10(INPUT_GATE_RMS)
RELEASE_DBFS_PER_UPDATE = 0.35  # ~60dB/s at the reading cadence: fast up, slow down

BACKGROUND = QColor("#2a303c")
BELOW_GATE = QColor("#55627a")
ABOVE_GATE = QColor("#7cb518")
GATE_TICK = QColor("#9ec3ef")
GATE_TICK_ACTIVE = QColor("#ffffff")
HANDLE = QColor("#6b7d99")
HANDLE_ACTIVE = QColor("#cfe2ff")


def fill_fraction(level_dbfs: float) -> float:
    lo, hi = RANGE_DBFS
    return min(1.0, max(0.0, (level_dbfs - lo) / (hi - lo)))


def gate_from_x(x: int, width: int) -> float:
    """Where a click at `x` puts the gate. The inverse of fill_fraction, in
    whole decibels — the bar is ~290px over a 60dB range, so finer steps would
    be a value nobody chose.

    The full range is open on purpose. Past about -35dBFS the gate starts
    eating real notes (measured on the violin scale recording), but that is a
    warning for the docs, not a fence: the point of the control is that this
    machine's room is not the one that number came from.
    """
    lo, hi = RANGE_DBFS
    fraction = min(1.0, max(0.0, x / max(width - 1, 1)))
    return round(lo + fraction * (hi - lo))


class InputLevelBar(QWidget):
    gate_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level_dbfs = RANGE_DBFS[0]
        self._gate_dbfs = DEFAULT_GATE_DBFS
        self._readout: QLabel | None = None
        self._active = False  # hovered or being dragged
        self.setFixedHeight(BAR_H + HANDLE_OVERHANG)
        self.setMouseTracking(True)  # hover needs moves with no button held
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    # --- state ---

    def set_level(self, level_dbfs: float) -> None:
        # instant attack, slow release — the standard meter ballistics that
        # keep short sounds visible
        if level_dbfs >= self._level_dbfs:
            self._level_dbfs = level_dbfs
        else:
            self._level_dbfs = max(level_dbfs, self._level_dbfs - RELEASE_DBFS_PER_UPDATE)
        self.update()

    def set_gate(self, gate_dbfs: float) -> None:
        """Show a gate someone else decided (restored settings, or the engine
        being the source of truth). Does not emit — this is the display
        following the value, not the user moving it."""
        self._gate_dbfs = gate_dbfs
        if self._active:
            self._place_readout()
        self.update()

    @property
    def gate_dbfs(self) -> float:
        return self._gate_dbfs

    # --- the control ---

    def _apply_x(self, x: int) -> None:
        gate = gate_from_x(x, self.width())
        if gate != self._gate_dbfs:
            self._gate_dbfs = gate
            self.gate_changed.emit(gate)
        self._place_readout()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._apply_x(int(event.position().x()))

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_x(int(event.position().x()))

    def enterEvent(self, event) -> None:
        self._active = True
        self._place_readout()
        self.update()

    def leaveEvent(self, event) -> None:
        self._active = False
        if self._readout is not None:
            self._readout.hide()
        self.update()

    # --- the hover readout ---

    def _place_readout(self) -> None:
        """Put the number above the tick, overlapping whatever is up there."""
        host = self.parentWidget()
        if host is None:
            return
        if self._readout is None or self._readout.parentWidget() is not host:
            self._readout = QLabel(host)
            self._readout.setStyleSheet(
                "background-color: #1c222c; color: #dce6f5;"
                " padding: 1px 4px; border: 1px solid #55627a;"
            )
        label = self._readout
        label.setText(f"{self._gate_dbfs:.0f} dB")
        label.adjustSize()

        tick_x = int(fill_fraction(self._gate_dbfs) * self.width())
        top_left = self.mapTo(host, QPoint(tick_x, 0))
        x = min(max(top_left.x() - label.width() // 2, 0), host.width() - label.width())
        label.move(x, max(top_left.y() - label.height() - 1, 0))
        label.raise_()
        label.show()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w = self.width()
        # the widget is taller than the bar; the extra is the handle's room
        top = HANDLE_OVERHANG
        painter.fillRect(0, top, w, BAR_H, BACKGROUND)

        fill = int(fill_fraction(self._level_dbfs) * w)
        if fill > 0:
            color = ABOVE_GATE if self._level_dbfs >= self._gate_dbfs else BELOW_GATE
            painter.fillRect(0, top, fill, BAR_H, color)

        gate_x = int(fill_fraction(self._gate_dbfs) * w)
        painter.fillRect(
            gate_x - HANDLE_W // 2,
            0,
            HANDLE_W,
            self.height(),
            HANDLE_ACTIVE if self._active else HANDLE,
        )
        # grooves, and the middle one is where the gate actually is: a wide
        # thing to take hold of must still say which pixel it means
        painter.setPen(QPen(BACKGROUND, 1))
        for dx in (-2, 0, 2):
            painter.drawLine(gate_x + dx, 1, gate_x + dx, self.height() - 2)
        painter.setPen(QPen(GATE_TICK_ACTIVE if self._active else GATE_TICK, 1))
        painter.drawRect(gate_x - HANDLE_W // 2, 0, HANDLE_W - 1, self.height() - 1)
