"""The value bubble that thin drag controls show while the pointer is on them.

Two controls need it (the input gate on the level bar, the metronome's
volume) and both are a handful of pixels tall, so neither can hold text and
neither may grow to make room — the space would come out of the meter for the
whole session to serve a number wanted for a second.

So the label is parented to the *window* and overlaps whatever is above the
control, appearing on hover and going away on leave.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QLabel, QWidget

STYLE = (
    "background-color: #1c222c; color: #dce6f5;"
    " padding: 1px 4px; border: 1px solid #55627a;"
)


class HoverReadout:
    """One bubble, re-parented to whatever window its control ends up in."""

    def __init__(self) -> None:
        self.label: QLabel | None = None

    def show_for(self, control: QWidget, text: str, anchor_x: int) -> None:
        """Put `text` just above `control`, centred on `anchor_x` (in the
        control's own coordinates) and kept inside the window."""
        host = control.parentWidget()
        if host is None:
            return  # not in a window yet; nothing to overlap
        if self.label is None or self.label.parentWidget() is not host:
            self.label = QLabel(host)
            self.label.setStyleSheet(STYLE)
        label = self.label
        label.setText(text)
        label.adjustSize()

        top_left = control.mapTo(host, QPoint(anchor_x, 0))
        x = min(max(top_left.x() - label.width() // 2, 0), host.width() - label.width())
        label.move(x, max(top_left.y() - label.height() - 1, 0))
        label.raise_()
        label.show()

    def hide(self) -> None:
        if self.label is not None:
            self.label.hide()

    @property
    def visible(self) -> bool:
        return self.label is not None and self.label.isVisible()
