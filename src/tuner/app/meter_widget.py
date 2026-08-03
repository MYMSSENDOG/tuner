"""Analog cent meter widget (-50..+50) with needle, cents badge and note bar."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from tuner.app.engine import TunerReading
from tuner.app.meter_model import needle_angle_deg, zone_for_cents
from tuner.core.tracker import State

BACKGROUND = QColor("#3b4252")
TICK_COLOR = QColor("#7f9ec7")
IN_TUNE_TICK_COLOR = QColor("#a5d631")
LABEL_COLOR = QColor("#6d8cb5")
NEEDLE_COLOR = QColor("#e03131")
DIM_TEXT = QColor("#55627a")

ZONE_COLORS = {
    "green": QColor("#7cb518"),
    "orange": QColor("#f08c00"),
    "red": QColor("#d6336c"),
}


class MeterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._reading: TunerReading | None = None
        self.setMinimumSize(420, 420)

    def set_reading(self, reading: TunerReading) -> None:
        self._reading = reading
        self.update()

    def _font(self, point_size: int, bold: bool = False) -> QFont:
        # derive from the system default — a hardcoded family would silently
        # fall back (and warn) on platforms that lack it
        font = self.font()
        font.setPointSize(max(1, point_size))
        font.setBold(bold)
        return font

    # geometry helpers -----------------------------------------------------

    def _pivot_and_radius(self) -> tuple[QPointF, float]:
        w = self.width()
        h = self.height() - self._note_bar_height()
        pivot = QPointF(w / 2, h * 0.92)
        radius = min(w * 0.46, h * 0.82)
        return pivot, radius

    def _note_bar_height(self) -> int:
        return int(self.height() * 0.35)

    @staticmethod
    def _point(pivot: QPointF, radius: float, cents: float) -> QPointF:
        angle = math.radians(needle_angle_deg(cents) - 90.0)
        return QPointF(
            pivot.x() + radius * math.cos(angle),
            pivot.y() + radius * math.sin(angle),
        )

    # painting --------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)

        pivot, radius = self._pivot_and_radius()
        self._draw_scale(painter, pivot, radius)
        self._draw_header(painter)
        self._draw_note_bar(painter)

        reading = self._reading
        if reading is not None and reading.state is State.OK and reading.note is not None:
            self._draw_needle(painter, pivot, radius, reading.note.cents)
            self._draw_cents_badge(painter, pivot, radius, reading.note.cents)
        elif reading is not None and reading.state is State.NOISY:
            painter.setPen(DIM_TEXT)
            painter.setFont(self._font(22, bold=True))
            painter.drawText(
                QRectF(0, pivot.y() - radius * 0.45, self.width(), 40),
                Qt.AlignmentFlag.AlignCenter,
                "NOISY",
            )

    def _draw_scale(self, painter: QPainter, pivot: QPointF, radius: float) -> None:
        painter.setFont(self._font(10))
        for cents in range(-50, 51):
            major = cents % 10 == 0
            in_tune_region = abs(cents) <= 10
            color = IN_TUNE_TICK_COLOR if in_tune_region else TICK_COLOR
            painter.setPen(QPen(color, 2.4 if major else 1.2))
            outer = radius * (1.06 if major else 1.03)
            inner = radius * 0.95 if major else radius * 0.985
            painter.drawLine(
                self._point(pivot, inner, cents), self._point(pivot, outer, cents)
            )
            if major:
                painter.setPen(IN_TUNE_TICK_COLOR if in_tune_region else LABEL_COLOR)
                label_pos = self._point(pivot, radius * 1.16, cents)
                rect = QRectF(label_pos.x() - 18, label_pos.y() - 9, 36, 18)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{cents:+d}".replace("+0", "0"))

    def _draw_needle(self, painter: QPainter, pivot: QPointF, radius: float, cents: float) -> None:
        painter.setPen(QPen(NEEDLE_COLOR, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(pivot, self._point(pivot, radius * 0.99, cents))

    def _draw_cents_badge(self, painter: QPainter, pivot: QPointF, radius: float, cents: float) -> None:
        text = f"{cents:+.0f}¢"
        rect = QRectF(pivot.x() + radius * 0.18, pivot.y() - radius * 0.42, radius * 0.62, radius * 0.28)
        painter.setPen(QPen(LABEL_COLOR, 1.5))
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(self._font(int(radius * 0.13), bold=True))
        painter.setPen(QColor("#9ec3ef"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_header(self, painter: QPainter) -> None:
        reading = self._reading
        if reading is None or reading.note is None:
            return
        painter.setPen(QColor("#6fa8dc"))
        painter.setFont(self._font(16, bold=True))
        text = f"{reading.note.label}: {reading.note.freq_hz:.0f}Hz"
        painter.drawText(
            QRectF(0, 8, self.width(), 28), Qt.AlignmentFlag.AlignCenter, text
        )

    def _draw_note_bar(self, painter: QPainter) -> None:
        bar_h = self._note_bar_height()
        rect = QRectF(0, self.height() - bar_h, self.width(), bar_h)
        reading = self._reading
        if reading is not None and reading.state is State.OK and reading.note is not None:
            painter.fillRect(rect, ZONE_COLORS[zone_for_cents(reading.note.cents)])
            painter.setPen(QColor("#20301a"))
            painter.setFont(self._font(int(bar_h * 0.62), bold=True))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, reading.note.name)
        else:
            painter.fillRect(rect, QColor("#333c4d"))
            painter.setPen(DIM_TEXT)
            painter.setFont(self._font(int(bar_h * 0.18)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "—")
