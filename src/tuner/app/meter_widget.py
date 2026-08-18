"""Analog cent meter widget (-50..+50) with needle, cents badge and note badge."""

from __future__ import annotations

import math
import time

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


HOLD_DISPLAY_S = 4.0  # keep the last pitch on screen (ghosted) after sound stops


class MeterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._reading: TunerReading | None = None
        self._last_ok: TunerReading | None = None
        self._last_ok_at = 0.0
        self.setMinimumSize(380, 260)

    def set_reading(self, reading: TunerReading) -> None:
        self._reading = reading
        if reading.state is State.OK:
            self._last_ok = reading
            self._last_ok_at = time.monotonic()
        self.update()

    def _display_reading(self) -> tuple[TunerReading | None, bool]:
        """The reading to draw and whether it is a ghost (held after sound
        stopped, so the player can still see where the last note landed)."""
        if self._reading is not None and self._reading.state is State.OK:
            return self._reading, False
        if self._last_ok is not None and time.monotonic() - self._last_ok_at <= HOLD_DISPLAY_S:
            return self._last_ok, True
        return None, False

    def _font(self, point_size: int, bold: bool = False) -> QFont:
        # derive from the system default — a hardcoded family would silently
        # fall back (and warn) on platforms that lack it
        font = self.font()
        font.setPointSize(max(1, point_size))
        font.setBold(bold)
        return font

    # geometry helpers -----------------------------------------------------

    def _pivot_and_radius(self) -> tuple[QPointF, float]:
        top = self._top_strip_height()
        w = self.width()
        h = self.height() - top
        pivot = QPointF(w / 2, top + h * 0.92)
        radius = min(w * 0.46, h * 0.82)
        return pivot, radius

    def _top_strip_height(self) -> int:
        """Band above the dial holding the frequency readout and note badge.

        The dial only occupies the lower ~55% of its bounding box (the needle
        sweeps +-50 deg from a bottom-centre pivot), so the corners above it
        were dead space — the note now lives there instead of in a bar below,
        which is what makes the window short.
        """
        return max(46, int(self.height() * 0.22))

    def _note_badge_rect(self) -> QRectF:
        strip = self._top_strip_height()
        pad = strip * 0.12
        height = strip - 2 * pad
        width = height * 2.1
        return QRectF(self.width() - pad - width, pad, width, height)

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
        display, ghost = self._display_reading()
        self._draw_scale(painter, pivot, radius)
        self._draw_header(painter, display)
        self._draw_note_badge(painter, display, ghost)

        reading = self._reading
        if display is not None and display.note is not None:
            if ghost:
                painter.setOpacity(0.4)
            self._draw_needle(painter, pivot, radius, display.note.cents)
            self._draw_cents_badge(painter, pivot, radius, display.note.cents)
            painter.setOpacity(1.0)
        if reading is not None and reading.state is State.NOISY:
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

    def _draw_header(self, painter: QPainter, display: TunerReading | None) -> None:
        """Measured frequency, left of the note badge. The note name itself is
        the badge's job now, so it is no longer repeated here."""
        if display is None or display.note is None:
            return
        strip = self._top_strip_height()
        painter.setPen(QColor("#6fa8dc"))
        painter.setFont(self._font(int(strip * 0.26), bold=True))
        painter.drawText(
            QRectF(strip * 0.3, 0, self.width() / 2, strip),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{display.note.freq_hz:.0f}Hz",
        )

    def _draw_note_badge(self, painter: QPainter, display: TunerReading | None, ghost: bool) -> None:
        rect = self._note_badge_rect()
        radius = rect.height() * 0.22
        if display is not None and display.note is not None:
            if ghost:
                painter.setOpacity(0.4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ZONE_COLORS[zone_for_cents(display.note.cents)])
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(QColor("#20301a"))
            painter.setFont(self._font(int(rect.height() * 0.52), bold=True))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, display.note.label)
            painter.setOpacity(1.0)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#333c4d"))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setPen(DIM_TEXT)
            painter.setFont(self._font(int(rect.height() * 0.4)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "—")
        painter.setBrush(Qt.BrushStyle.NoBrush)
