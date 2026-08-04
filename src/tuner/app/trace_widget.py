"""Scrolling pitch trace: cent deviation over the last few seconds.

Each reading plots its own note's cent deviation (same quantity as the
meter needle), so a steady tone draws a flat line, vibrato draws its wave,
and silence leaves a gap.
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from tuner.app.engine import TunerReading
from tuner.core.tracker import State

SPAN_S = 6.0  # visible history
RANGE_CENTS = 50.0

BACKGROUND = QColor("#2f3542")
CENTER_LINE = QColor("#5a8a3a")
GUIDE_LINE = QColor("#46536b")
TRACE = QColor("#9ec3ef")


class PitchTraceWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: deque[tuple[float, float | None]] = deque()
        self.setFixedHeight(90)

    def add_reading(self, reading: TunerReading) -> None:
        now = time.monotonic()
        cents = (
            reading.note.cents
            if reading.state is State.OK and reading.note is not None
            else None
        )
        self._points.append((now, cents))
        cutoff = now - SPAN_S
        while self._points and self._points[0][0] < cutoff:
            self._points.popleft()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)
        w, h = self.width(), self.height()

        for cents, color in ((25.0, GUIDE_LINE), (-25.0, GUIDE_LINE), (0.0, CENTER_LINE)):
            y = h / 2 - cents / RANGE_CENTS * (h / 2 - 4)
            painter.setPen(QPen(color, 1))
            painter.drawLine(0, int(y), w, int(y))

        if not self._points:
            return
        now = time.monotonic()
        painter.setPen(QPen(TRACE, 1.6))
        segment: list[QPointF] = []
        for t, cents in self._points:
            if cents is None:
                if len(segment) > 1:
                    painter.drawPolyline(segment)
                segment = []
                continue
            x = (t - now + SPAN_S) / SPAN_S * w
            clamped = max(-RANGE_CENTS, min(RANGE_CENTS, cents))
            y = h / 2 - clamped / RANGE_CENTS * (h / 2 - 4)
            segment.append(QPointF(x, y))
        if len(segment) > 1:
            painter.drawPolyline(segment)
