"""The visual metronome: a lane of falling tiles beside the meter, and the
beat's number slamming in as each one lands.

For when the click cannot be heard — a loud instrument, earphones off, a
noisy room. Chosen from mock-ups watched with the sound off: what reads in
peripheral vision is a large area changing brightness at once, and seeing the
next beat coming. So this has both:

- **anticipation**: tiles that fill most of the gap between beats fall down a
  narrow lane; a tile's bottom edge reaching the line *is* the beat, and the
  next few are always in view.
- **the moment**: the whole lane flashes on the beat (gold on the first beat
  of the bar), and the beat's number (1·2·3·4) slams in large beside the line,
  then stays dimly until the next one replaces it — a missed beat can still be
  read off.

Timing comes from the audio clock, never from this widget's timer: every
repaint asks `MetronomeService.beat_view` where the beat currently *heard*
falls, so the tile lands on the click however unevenly Qt repaints. The timer
only decides how often to look (docs/metronome.md, 제1원칙).

It sits over the empty left margin of the meter and lets every mouse event
through — a drawn-over widget that eats clicks is a mistake this window has
already made once (the drawer handle).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from tuner.app.metronome import MetronomeService

LANE_W = 16
CALLOUT_ROOM = 34  # the beat number beside the lane, in the meter's empty margin
WIDTH = LANE_W + 6 + CALLOUT_ROOM
AHEAD = 3  # beats visible ahead
FRAME_MS = 16  # how often to look; the beat itself comes from the audio clock

FLASH_S = 0.16
SLAM_S = 0.12  # the number shrinks from large to its size in this long
HOLD_S = 0.18  # then stays at full strength this long before dimming
DIMMEST = 0.35  # and never fades below this until the next beat replaces it
TILE_SHARE = 0.78  # of the gap between beats a tile fills

LANE_BG = QColor(43, 49, 61, 216)
HIT_IDLE = QColor("#6b7a93")
NOTE = QColor("#9ec3ef")
ACCENT = QColor("#ffd166")
FLASH = QColor(220, 235, 255)
OUTLINE = QColor(20, 24, 32, 205)


def ease_out(k: float) -> float:
    return 1.0 - (1.0 - k) ** 3


class BeatLane(QWidget):
    def __init__(self, service: MetronomeService, parent=None):
        super().__init__(parent)
        self._service = service
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedWidth(WIDTH)
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self.update)
        self.hide()

    def set_running(self, running: bool) -> None:
        """Shown, and repainting, only while the metronome plays."""
        if running == self._timer.isActive():
            return
        if running:
            self._timer.start()
            self.show()
            self.raise_()
        else:
            self._timer.stop()
            self.hide()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    # --- geometry --------------------------------------------------------------

    def _hit_y(self) -> float:
        return self.height() - 6.0

    def _tile_bottom(self, seconds_until: float, period: float) -> float:
        travel = self._hit_y() - 4.0
        return self._hit_y() - max(seconds_until, 0.0) / (AHEAD * period) * travel

    # --- drawing -----------------------------------------------------------------

    def paintEvent(self, event) -> None:
        view = self._service.beat_view(AHEAD)
        if view is None:
            return
        bar = self._service.beats_per_bar
        period = 60.0 / self._service.bpm
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        lane = QRectF(0, 0, LANE_W, self._hit_y() + 6)
        lane_path = QPainterPath()
        lane_path.addRoundedRect(lane, 4, 4)
        painter.fillPath(lane_path, LANE_BG)

        # tiles: the next few beats, plus the one just landed fading out
        painter.save()
        painter.setClipPath(lane_path)
        tile_h = (self._hit_y() - 4.0) / AHEAD * TILE_SHARE
        beats = list(view.seconds_until())
        since = view.seconds_since_last
        if view.last is not None and since is not None and since < 0.25:
            beats.insert(0, (view.last[0], -since))
        for number, until in beats:
            colour = QColor(ACCENT if number % bar == 0 else NOTE)
            colour.setAlphaF(0.25 if until < 0 else 0.9)
            bottom = self._tile_bottom(until, period)
            tile = QPainterPath()
            tile.addRoundedRect(QRectF(2, bottom - tile_h, LANE_W - 4, tile_h), 3, 3)
            painter.fillPath(tile, colour)
        painter.restore()

        # the moment: the lane flashes and the hit line lights
        accent = view.last is not None and view.last[0] % bar == 0
        strength = 0.0
        if since is not None and since < FLASH_S:
            strength = 1.0 - since / FLASH_S
            glow = QColor(ACCENT if accent else FLASH)
            glow.setAlphaF((0.7 if accent else 0.6) * strength)
            painter.fillPath(lane_path, glow)
        line = QColor(ACCENT if (accent and strength) else (FLASH if strength else HIT_IDLE))
        if strength:
            line.setAlphaF(0.3 + 0.7 * strength)
        painter.fillRect(QRectF(-2, self._hit_y(), LANE_W + 4, 2), line)

        if view.last is not None and since is not None and since < period * 1.02:
            self._draw_number(painter, (view.last[0] % bar) + 1, since, accent)

    def _draw_number(self, painter: QPainter, beat: int, since: float, accent: bool) -> None:
        """The beat's number: slams in large, settles, then holds dimly."""
        scale = 1.8 - 0.8 * ease_out(min(1.0, since / SLAM_S))
        alpha = 1.0 if since < HOLD_S else max(DIMMEST, 1.0 - (since - HOLD_S) * 2.0)
        font = QFont(self.font())
        font.setPixelSize(26)
        font.setWeight(QFont.Weight.ExtraBold)
        text = QPainterPath()
        text.addText(QPointF(0, 0), font, str(beat))
        box = text.boundingRect()
        painter.save()
        painter.translate(LANE_W + 6, self._hit_y() - 8)
        painter.scale(scale, scale)
        painter.translate(0, box.height() / 2 - 2)
        painter.setOpacity(alpha)
        painter.strokePath(text, QPen(OUTLINE, 4))
        painter.fillPath(text, ACCENT if accent else QColor("#ffffff"))
        painter.restore()
