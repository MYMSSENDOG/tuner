"""The big round buttons of the recorder: mic, stop, play, pause.

Voice recorders put one large circle in the middle of the panel and that is
the control — it is the thing you aim at without looking, which is the point
when your other hand is holding an instrument.

The glyphs are drawn, not typed. Same reason the metronome's menu icon is: a
character the font lacks renders as a box, and a control that disappears on
someone else's machine is not worth the two lines it saves.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton

MIC = "mic"
STOP = "stop"
PLAY = "play"
PAUSE = "pause"

RING_SPAN = 14  # room a ringed button leaves around itself, so rings are not clipped
RING_COLOR = QColor("#5a6c86")
DISABLED_FILL = QColor("#424b5c")
DISABLED_GLYPH = QColor("#6b7686")


def lighter(color: QColor, amount: int = 118) -> QColor:
    return color.lighter(amount)


class RoundButton(QAbstractButton):
    """A filled circle with a drawn glyph. Optionally ringed, as the Windows
    recorder rings the stop button while a take is running."""

    def __init__(
        self,
        glyph: str,
        diameter: int,
        fill: str,
        glyph_color: str,
        parent=None,
        ring_span: int = 0,
    ):
        super().__init__(parent)
        self._glyph = glyph
        self._diameter = diameter
        self._fill = QColor(fill)
        self._glyph_color = QColor(glyph_color)
        self._ringed = False
        # Only a button that will wear rings reserves room for them. The mic
        # never does, and reserving it anyway made that button's widget half
        # empty space in a panel that has none to spare.
        self.setFixedSize(diameter + 2 * ring_span, diameter + 2 * ring_span)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self.update()

    @property
    def glyph(self) -> str:
        return self._glyph

    def set_ringed(self, ringed: bool) -> None:
        """Rings mean 'this is live' — they are the only animation-free way to
        say so on a still button."""
        self._ringed = ringed
        self.update()

    def set_fill(self, fill: str) -> None:
        self._fill = QColor(fill)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = self._diameter / 2.0

        if self._ringed:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i, step in enumerate((6, 12, 18)):
                pen = QPen(RING_COLOR, 1)
                colour = QColor(RING_COLOR)
                colour.setAlpha(150 - i * 40)
                pen.setColor(colour)
                painter.setPen(pen)
                painter.drawEllipse(centre, radius + step, radius + step)

        if not self.isEnabled():
            fill, glyph_colour = DISABLED_FILL, DISABLED_GLYPH
        elif self.underMouse():
            fill, glyph_colour = lighter(self._fill), self._glyph_color
        else:
            fill, glyph_colour = self._fill, self._glyph_color

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(centre, radius, radius)

        painter.setBrush(glyph_colour)
        painter.setPen(Qt.PenStyle.NoPen)
        self._draw_glyph(painter, centre, radius, glyph_colour)

    def _draw_glyph(self, painter: QPainter, centre: QPointF, radius: float, colour: QColor) -> None:
        if self._glyph == STOP:
            side = radius * 0.62
            painter.drawRoundedRect(
                QRectF(centre.x() - side / 2, centre.y() - side / 2, side, side), 2.0, 2.0
            )
        elif self._glyph == PAUSE:
            bar_w, bar_h = radius * 0.2, radius * 0.72
            gap = radius * 0.22
            for dx in (-gap - bar_w, gap):
                painter.drawRoundedRect(
                    QRectF(centre.x() + dx, centre.y() - bar_h / 2, bar_w, bar_h), 1.5, 1.5
                )
        elif self._glyph == PLAY:
            side = radius * 0.78
            path = QPainterPath()
            # nudged right so the triangle *looks* centred: its visual centre
            # of mass sits left of its bounding box
            x = centre.x() - side * 0.3 + side * 0.12
            path.moveTo(x, centre.y() - side * 0.58)
            path.lineTo(x + side * 0.86, centre.y())
            path.lineTo(x, centre.y() + side * 0.58)
            path.closeSubpath()
            painter.drawPath(path)
        else:  # MIC — a capsule, the arc that cradles it, and a stand
            capsule_w = radius * 0.34
            capsule_h = radius * 0.66
            top = centre.y() - radius * 0.52
            painter.drawRoundedRect(
                QRectF(centre.x() - capsule_w / 2, top, capsule_w, capsule_h),
                capsule_w / 2,
                capsule_w / 2,
            )
            pen = QPen(colour, max(1.5, radius * 0.1))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            cradle = radius * 0.5
            painter.drawArc(
                QRectF(centre.x() - cradle, centre.y() - cradle * 0.55, cradle * 2, cradle * 1.5),
                180 * 16,
                180 * 16,
            )
            painter.drawLine(
                QPointF(centre.x(), centre.y() + cradle * 0.95),
                QPointF(centre.x(), centre.y() + radius * 0.6),
            )

    def enterEvent(self, event) -> None:
        self.update()

    def leaveEvent(self, event) -> None:
        self.update()

