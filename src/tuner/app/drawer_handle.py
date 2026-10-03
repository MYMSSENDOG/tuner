"""The tab on the window's right edge that opens the recordings drawer.

Eleven pixels wide, and that is the whole design problem. The window is
310x190 and sits on top of a music stand, so a handle cannot be a button with
a label — but it also cannot be a bare line, because this repo already learned
that a 1px tick says "here is the threshold" and not "take hold of this"
(docs/pitch-pipeline.md, the input gate's grip).

So: a half-disc welded to the edge. The left side is flush with the window and
has no border, the right side is a half-stadium — the shape itself says "this
belongs to the window, and it opens that way". Four shapes were drawn at real
size and compared; the waisted one was prettier at 3x magnification and nearly
straight at 11px, which is the size that counts (docs/recording.md).

The hit area is a little taller than the paint (nobody should have to hit
the exact pixel), and the widget is exactly that hit area and no more. It is
drawn over the tuner column's right edge, and anything it covers is taken
from the widgets underneath: they are siblings, so no event passes through.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

WIDTH = 11  # painted width, and the widget's width
HEIGHT = 44  # the tab; tall enough to find without being a column
HIT_MARGIN = 4  # extra pixels above and below that still count as a press
# The widget's whole size. It was once a full-height strip that only *painted*
# a tab in the middle - and swallowed every click on the last 11px of the level
# bar and the trace beneath it.
OUTER_HEIGHT = HEIGHT + 2 * HIT_MARGIN

BODY = QColor("#4a5568")
BODY_ACTIVE = QColor("#5d6c84")
EDGE = QColor("#5a6679")
EDGE_ACTIVE = QColor("#9ec3ef")
ARROW = QColor("#cbd6e6")
ARROW_ACTIVE = QColor("#ffffff")

# The chevron is drawn, not typed. Same reason the metronome's menu icon is:
# a glyph the font lacks renders as a box, and a control that disappears on
# someone else's machine is not worth the two lines it saves. At 11px wide it
# is three pixels of arm anyway.
CHEVRON_ARM = 3


class DrawerHandle(QWidget):
    """A vertical tab. Click toggles; the arrow says which way it will go."""

    toggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._open = False
        self._active = False
        self.setFixedSize(WIDTH, OUTER_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("녹음 목록 (Alt+L)")

    def set_open(self, is_open: bool) -> None:
        self._open = is_open
        self.update()

    def _tab_top(self) -> int:
        return HIT_MARGIN

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit()

    def enterEvent(self, event) -> None:
        self._active = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._active = False
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = float(self._tab_top())
        height = float(HEIGHT)

        # Left edge flush with the window (drawn 1px off-widget so its border
        # does not show), right edge one half-ellipse: 11 across, half the tab
        # tall. A circle would need 22px of width that this window does not
        # have, and the ellipse is what the design was drawn with.
        path = QPainterPath()
        path.moveTo(-1.0, top)
        path.lineTo(0.0, top)
        path.arcTo(-WIDTH + 0.5, top + 0.5, 2 * WIDTH - 1.0, height - 1.0, 90.0, -180.0)
        path.lineTo(-1.0, top + height)
        path.closeSubpath()

        painter.setBrush(BODY_ACTIVE if self._active else BODY)
        painter.setPen(QPen(EDGE_ACTIVE if self._active else EDGE, 1))
        painter.drawPath(path)

        # A chevron pointing the way the drawer will go: left while closed
        # (toward where the panel appears), right once it is open.
        painter.setPen(QPen(ARROW_ACTIVE if self._active else ARROW, 1))
        mid_x = WIDTH / 2.0 + 1.0
        mid_y = top + height / 2.0
        tip = mid_x + (CHEVRON_ARM / 2.0 if self._open else -CHEVRON_ARM / 2.0)
        back = mid_x - (CHEVRON_ARM / 2.0 if self._open else -CHEVRON_ARM / 2.0)
        painter.drawLine(QPointF(back, mid_y - CHEVRON_ARM), QPointF(tip, mid_y))
        painter.drawLine(QPointF(tip, mid_y), QPointF(back, mid_y + CHEVRON_ARM))
