"""Scrolling pitch trace: cent deviation of recent readings.

- y axis: cent deviation from each reading's own note — center line is
  "in tune" (0¢), full scale ±50¢.
- x axis: reading count, not wall time — when there is no input the trace
  freezes instead of scrolling away (silence contributes a single gap).
- note changes draw a boundary line labeled with the new note ("B3"), and
  the leftmost visible segment is labeled too, so the wave is always
  attributable to a note.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from tuner.app.engine import TunerReading
from tuner.core.tracker import State

RANGE_CENTS = 50.0
MAX_POINTS = 1000  # ~6s of continuous readings at the default hop

BACKGROUND = QColor("#2f3542")
CENTER_LINE = QColor("#5a8a3a")
GUIDE_LINE = QColor("#46536b")
TRACE = QColor("#9ec3ef")
LABEL = QColor("#8fa8c7")
AXIS_TEXT = QColor("#55627a")

_GAP = (None, None)


class PitchTraceWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # (cents, note_label); a single (None, None) entry marks silence
        self._points: deque[tuple[float | None, str | None]] = deque(maxlen=MAX_POINTS)
        self.setFixedHeight(90)

    def add_reading(self, reading: TunerReading) -> None:
        if reading.state is State.OK and reading.note is not None:
            self._points.append((reading.note.cents, reading.note.label))
        elif self._points and self._points[-1] != _GAP:
            self._points.append(_GAP)  # one gap, then freeze until sound returns
        else:
            return  # nothing changed; do not repaint either
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)
        w, h = self.width(), self.height()

        def y_at(cents: float) -> float:
            clamped = max(-RANGE_CENTS, min(RANGE_CENTS, cents))
            return h / 2 - clamped / RANGE_CENTS * (h / 2 - 4)

        for guide, color in ((25.0, GUIDE_LINE), (-25.0, GUIDE_LINE), (0.0, CENTER_LINE)):
            painter.setPen(QPen(color, 1))
            painter.drawLine(0, int(y_at(guide)), w, int(y_at(guide)))

        font = self.font()
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(AXIS_TEXT)
        painter.drawText(4, int(y_at(RANGE_CENTS)) + 10, "+50")
        painter.drawText(4, int(y_at(0)) - 3, "0")
        painter.drawText(4, int(y_at(-RANGE_CENTS)) - 2, "-50")

        points = list(self._points)
        if not points:
            return
        step = w / MAX_POINTS
        x0 = w - len(points) * step  # newest reading pinned to the right edge

        # group into segments of one note (gaps and label changes split)
        segments: list[tuple[str | None, list[QPointF]]] = []
        for i, (cents, label) in enumerate(points):
            if cents is None:
                segments.append((None, []))
                continue
            if not segments or segments[-1][0] != label:
                segments.append((label, []))
            segments[-1][1].append(QPointF(x0 + i * step, y_at(cents)))

        min_label_points = 8  # ~50ms; transition flicker doesn't earn a label
        labeled_before = False
        for label, pts in segments:
            if len(pts) > 1:
                painter.setPen(QPen(TRACE, 1.6))
                painter.drawPolyline(pts)
            if label is None:
                continue
            if len(pts) >= min_label_points:
                x = pts[0].x()
                if labeled_before:  # a change line; the first label needs none
                    painter.setPen(QPen(GUIDE_LINE, 1, Qt.PenStyle.DashLine))
                    painter.drawLine(int(x), 0, int(x), h)
                painter.setPen(LABEL)
                painter.drawText(int(x) + 3, 12, label)
                labeled_before = True
