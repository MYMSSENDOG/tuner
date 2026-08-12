"""Thin input-level bar with the detection gate marked.

Answers "is sound reaching the tuner at all?" at a glance: the fill shows
the microphone level, the tick shows the gate below which the detector
refuses to judge — so "signal present but too quiet" and "no signal" look
different, instead of both being a blank meter.
"""

from __future__ import annotations

import math

from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from tuner.core.detector import INPUT_GATE_RMS

RANGE_DBFS = (-60.0, 0.0)
GATE_DBFS = 20.0 * math.log10(INPUT_GATE_RMS)  # single source of truth
RELEASE_DBFS_PER_UPDATE = 0.35  # ~60dB/s at the reading cadence: fast up, slow down

BACKGROUND = QColor("#2a303c")
BELOW_GATE = QColor("#55627a")
ABOVE_GATE = QColor("#7cb518")
GATE_TICK = QColor("#9ec3ef")


def fill_fraction(level_dbfs: float) -> float:
    lo, hi = RANGE_DBFS
    return min(1.0, max(0.0, (level_dbfs - lo) / (hi - lo)))


class InputLevelBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._level_dbfs = RANGE_DBFS[0]
        self.setFixedHeight(8)

    def set_level(self, level_dbfs: float) -> None:
        # instant attack, slow release — the standard meter ballistics that
        # keep short sounds visible
        if level_dbfs >= self._level_dbfs:
            self._level_dbfs = level_dbfs
        else:
            self._level_dbfs = max(level_dbfs, self._level_dbfs - RELEASE_DBFS_PER_UPDATE)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKGROUND)
        w, h = self.width(), self.height()

        fill = int(fill_fraction(self._level_dbfs) * w)
        if fill > 0:
            color = ABOVE_GATE if self._level_dbfs >= GATE_DBFS else BELOW_GATE
            painter.fillRect(0, 0, fill, h, color)

        gate_x = int(fill_fraction(GATE_DBFS) * w)
        painter.setPen(QPen(GATE_TICK, 1))
        painter.drawLine(gate_x, 0, gate_x, h)
