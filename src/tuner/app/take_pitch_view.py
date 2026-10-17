"""The intonation of a whole take, drawn under the sound of it.

Horizontal is the take's time; vertical is how far each reading was from the
note it was shown as, in cents, across the meter's own ±50. The colours are the
meter's too — green inside ±8, orange to ±15, red beyond — so a stretch of red
here means exactly what a red needle meant at the time. Note names sit above
the trace wherever the note changes, and a line marks where playback is.

Cents rather than Hz because the question a player brings to this is "where
was I out of tune", not "what frequency was that": in Hz a scale is a
staircase and every step looks equally fine.

It fills in from the left while the take is being analysed (which runs at
about three times real time), so it is usable before it is complete. Clicking
or dragging moves playback there.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from tuner.app.meter_model import GREEN_LIMIT, ORANGE_LIMIT, zone_for_cents
from tuner.app.take_analysis import PitchTrack

HEIGHT = 104  # what it asks for; it gives way down to MIN_HEIGHT
MIN_HEIGHT = 40  # the tuner at its shortest still has room for the drawer
RANGE_CENTS = 50.0
LABEL_BAND = 14  # room at the top for note names
# A note name is written only where the note lasted long enough to read; a
# boundary-straddling vibrato flips the name many times a second and would
# write a smear of letters.
MIN_LABEL_RUN_S = 0.25

BACKGROUND = QColor("#2b313d")
IN_TUNE_BAND = QColor(124, 181, 24, 28)  # the ±8 band, faintly
CENTRE = QColor("#5a8a3a")
GUIDE = QColor("#3e4757")
LABEL = QColor("#8fa8c7")
PLAYHEAD = QColor("#dce6f5")
PENDING = QColor(255, 255, 255, 10)
PENDING_TEXT = QColor("#6b7686")
ZONES = {"green": QColor("#a5d631"), "orange": QColor("#f0a020"), "red": QColor("#e0607a")}


class TakePitchView(QWidget):
    seeked = Signal(float)  # 0..1 of the take

    def __init__(self, parent=None):
        super().__init__(parent)
        self._track: PitchTrack | None = None
        self._position_s = 0.0
        self._message = ""
        self.setMinimumHeight(MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("녹음의 음정 — 초록 ±8¢, 주황 ±15¢, 빨강은 그 밖")

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), HEIGHT)

    # --- what the window tells it -------------------------------------------

    def set_track(self, track: PitchTrack | None) -> None:
        self._track = track
        self._message = ""
        self.update()

    def set_message(self, text: str) -> None:
        """Shown in place of the graph (e.g. a take that could not be read)."""
        self._message = text
        self.update()

    def set_position(self, seconds: float) -> None:
        self._position_s = seconds
        self.update()

    @property
    def track(self) -> PitchTrack | None:
        return self._track

    # --- geometry -----------------------------------------------------------

    def _plot(self) -> QRectF:
        return QRectF(0, LABEL_BAND, self.width(), self.height() - LABEL_BAND - 2)

    def _x(self, seconds: float) -> float:
        duration = self._track.duration_s if self._track else 0.0
        return 0.0 if duration <= 0 else seconds / duration * self.width()

    def _y(self, cents: float, plot: QRectF) -> float:
        clamped = max(-RANGE_CENTS, min(RANGE_CENTS, cents))
        return plot.center().y() - clamped / RANGE_CENTS * plot.height() / 2

    # --- input -----------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        self._seek_to(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek_to(event.position().x())

    def _seek_to(self, x: float) -> None:
        if self.width() > 0:
            self.seeked.emit(min(1.0, max(0.0, x / self.width())))

    # --- drawing ---------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKGROUND)
        plot = self._plot()

        # the in-tune band and the guides: centre, and the meter's ±50 edges
        band = self._y(GREEN_LIMIT, plot), self._y(-GREEN_LIMIT, plot)
        painter.fillRect(QRectF(0, band[0], self.width(), band[1] - band[0]), IN_TUNE_BAND)
        painter.setPen(QPen(GUIDE, 1, Qt.PenStyle.DotLine))
        for cents in (ORANGE_LIMIT, -ORANGE_LIMIT):
            y = self._y(cents, plot)
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
        painter.setPen(QPen(CENTRE, 1))
        painter.drawLine(QPointF(0, plot.center().y()), QPointF(self.width(), plot.center().y()))

        if self._message:
            self._centred_text(painter, self._message)
            return
        track = self._track
        if track is None:
            self._centred_text(painter, "분석 중…")
            return

        self._draw_trace(painter, track, plot)
        self._draw_labels(painter, track)

        if not track.complete:
            done_x = self._x(track.analysed_s)
            painter.fillRect(QRectF(done_x, 0, self.width() - done_x, self.height()), PENDING)
            share = track.analysed_s / track.duration_s if track.duration_s else 0.0
            painter.setPen(PENDING_TEXT)
            painter.drawText(
                QRectF(done_x, 0, self.width() - done_x - 4, LABEL_BAND),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"분석 중 {share:.0%}",
            )

        x = self._x(self._position_s)
        painter.setPen(QPen(PLAYHEAD, 1))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    def _draw_trace(self, painter: QPainter, track: PitchTrack, plot: QRectF) -> None:
        """One mark per pixel column: the median of the readings in it.

        A take is thousands of readings across a couple of hundred pixels;
        drawing every one would paint a solid smear wherever there is
        vibrato. The median keeps the line where the pitch actually sat.
        """
        width = max(self.width(), 1)
        columns: dict[int, list[float]] = {}
        for t, c in zip(track.times, track.cents, strict=True):
            if math.isnan(c):
                continue
            columns.setdefault(min(int(self._x(t)), width - 1), []).append(c)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        medians = {}
        for column, values in columns.items():
            values.sort()
            medians[column] = values[len(values) // 2]
        # Neighbouring columns are joined into a line, so a vibrato reads as
        # the curve it is rather than a scatter of dots; a column with no
        # reading (silence, a gap between notes) breaks the line there.
        for column in sorted(medians):
            here = medians[column]
            point = QPointF(column + 0.5, self._y(here, plot))
            before = medians.get(column - 1)
            colour = ZONES[zone_for_cents(here)]
            if before is None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(colour)
                painter.drawEllipse(point, 1.2, 1.2)
            else:
                painter.setPen(QPen(colour, 1.6))
                painter.drawLine(QPointF(column - 0.5, self._y(before, plot)), point)

    def _draw_labels(self, painter: QPainter, track: PitchTrack) -> None:
        font = QFont(painter.font())
        font.setPointSizeF(max(font.pointSizeF() - 1.5, 6.5))
        painter.setFont(font)
        painter.setPen(LABEL)
        last_x = -1e9
        for label, start, end in _runs(track):
            if end - start < MIN_LABEL_RUN_S:
                continue
            x = self._x(start)
            if x - last_x < 22:  # never write two names on top of each other
                continue
            painter.drawText(QPointF(x + 1, LABEL_BAND - 3), label)
            last_x = x

    def _centred_text(self, painter: QPainter, text: str) -> None:
        painter.setPen(PENDING_TEXT)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


def _runs(track: PitchTrack) -> list[tuple[str, float, float]]:
    """(label, start, end) for each stretch the same note was shown."""
    runs: list[tuple[str, float, float]] = []
    current: str | None = None
    start = 0.0
    for t, label in zip(track.times, track.labels, strict=True):
        if label != current:
            if current is not None:
                runs.append((current, start, t))
            current, start = label, t
    if current is not None and track.times:
        runs.append((current, start, track.times[-1]))
    return runs
