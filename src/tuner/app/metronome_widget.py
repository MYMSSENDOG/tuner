"""The metronome's one row of controls: play, tempo down, tempo, tempo up.

Deliberately four small widgets and no panel. The row sits under the tuner's
own controls and above the meter, so every pixel it takes is one the meter
does not get, and the window's width floor is set by whichever row is widest
(the device combo's row, at 294px — this one must stay under that).

Two ways to set the tempo, because they answer different questions:
  - the +/- buttons for "a bit faster", answered in place with no dialog;
  - the number itself for "138", answered by typing it.
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tuner.app.metronome import MetronomeService
from tuner.core.metronome import (
    CLICK_SOUNDS,
    MAX_BPM,
    MAX_VOLUME,
    MIN_BPM,
    MIN_VOLUME,
)

# How long the device stays open for one audition. Long enough for the
# longest sound (45ms) and its tail, short enough that clicking down a list
# does not queue up a backlog of open streams.
PREVIEW_MS = 250

PLAY_TEXT = "▶"
STOP_TEXT = "■"
BPM_STEP = 1.0

# The sound button's three bars, drawn rather than typed. U+2630 renders as a
# box wherever the font lacks it, and a control that disappears on someone
# else's machine is not worth the two lines this saves.
MENU_LINES = 3
MENU_LINE_W = 12
MENU_LINE_GAP = 4
MENU_LINE_COLOR = QColor("#c7d2e3")

# Long enough to drag comfortably. The row can afford it — the window's width
# floor is set by the controls row above (294px), and this row still comes in
# under that with room to spare.
# A bar with a round handle. The bar is the value at a glance; the handle is
# what says it can be moved — a bare fill reads as a meter, which is exactly
# how the first version of this was misread.
VOLUME_W = 88
# The row is 22px tall and set by the buttons, so 18 costs nothing: the track
# keeps its 10 and the extra is the handle standing proud of it.
VOLUME_H = 18
VOLUME_TRACK_H = 10
VOLUME_HANDLE_D = 14

VOLUME_TRACK = QColor("#2a303c")
VOLUME_TRACK_EDGE = QColor("#454f61")  # a slot, not a painted rectangle
VOLUME_FILL = QColor("#55627a")
VOLUME_FILL_ACTIVE = QColor("#7d90ad")
VOLUME_HANDLE = QColor("#9aa9c2")
VOLUME_HANDLE_ACTIVE = QColor("#cfe2ff")
VOLUME_HANDLE_EDGE = QColor("#2a303c")


def volume_from_x(x: int, width: int) -> float:
    """Where a click at `x` puts the volume, 0..1.

    Absolute, like the input gate's bar above it: the handle is visible, so
    pressing anywhere on the track means "put it there" rather than "start
    from wherever it was".

    Snapped to 5% — 88px is 20 detents, which is finer than anyone chooses a
    metronome level and coarse enough that the handle settles.
    """
    fraction = min(1.0, max(0.0, x / max(width - 1, 1)))
    span = MAX_VOLUME - MIN_VOLUME
    return MIN_VOLUME + round(fraction * span * 20.0) / 20.0


class MenuButton(QPushButton):
    """A button that says "there is a list behind me" and nothing else."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(MENU_LINE_COLOR, 2))
        span = (MENU_LINES - 1) * MENU_LINE_GAP
        x, y = (self.width() - MENU_LINE_W) // 2, (self.height() - span) // 2
        for i in range(MENU_LINES):
            painter.drawLine(x, y + i * MENU_LINE_GAP, x + MENU_LINE_W, y + i * MENU_LINE_GAP)


class SoundDialog(QDialog):
    """The four sounds, as a list you can hear.

    A list and not a cycling button because the names are worth seeing at
    once; hearing is still what decides, so moving the selection plays the
    sound rather than waiting for OK. Cancel puts back the one you came in
    with — an audition must not be able to change the setting by accident.
    """

    def __init__(self, service: MetronomeService, parent=None):
        super().__init__(parent)
        self.setWindowTitle("메트로놈 소리")
        self._service = service
        self._original = service.sound

        self._list = QListWidget()
        self._list.addItems(list(CLICK_SOUNDS))
        self._list.setCurrentRow(list(CLICK_SOUNDS).index(service.sound))
        self._list.currentTextChanged.connect(self._audition)
        self._list.itemDoubleClicked.connect(lambda _: self.accept())

        # one timer, restarted per audition: clicking down the list must not
        # leave a stream open per row
        self._silence = QTimer(self)
        self._silence.setSingleShot(True)
        self._silence.setInterval(PREVIEW_MS)
        self._silence.timeout.connect(self._service.end_preview)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._list)
        layout.addWidget(buttons)

    def _audition(self, name: str) -> None:
        # no output device is ordinary; the list still selects and OK still
        # applies, you just do not get to hear it first
        with contextlib.suppress(RuntimeError, OSError):
            self._service.preview(name)
        self._silence.start()

    def done(self, result: int) -> None:
        self._silence.stop()
        self._service.end_preview()
        if result != QDialog.DialogCode.Accepted:
            self._service.set_sound(self._original)
        super().done(result)

    @property
    def chosen(self) -> str:
        return self._list.currentItem().text()


class VolumeBar(QWidget):
    """The metronome's loudness, as a bar with a handle you drag.

    No readout: what is being decided here is not a number but whether it is
    too loud, and the answer to that is in the room. The fill says roughly
    where it sits, which is all a volume needs to say.
    """

    volume_changed = Signal(float)

    def __init__(self, volume: float, parent=None):
        super().__init__(parent)
        self._volume = volume
        self._active = False
        self.setFixedSize(VOLUME_W, VOLUME_H)
        self.setMouseTracking(True)
        # the pointer stays the ordinary arrow. A resize cursor is what a
        # window edge does, and the handle already says this can be dragged
        self.setToolTip("메트로놈 소리 크기")

    @property
    def volume(self) -> float:
        return self._volume

    def set_volume(self, volume: float) -> None:
        """Follow a value someone else chose; does not emit."""
        self._volume = volume
        self.update()

    def _apply_x(self, x: int) -> None:
        volume = volume_from_x(x, self.width())
        if volume != self._volume:
            self._volume = volume
            self.volume_changed.emit(volume)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._apply_x(int(event.position().x()))

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_x(int(event.position().x()))

    def enterEvent(self, event) -> None:
        self._active = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._active = False
        self.update()

    def handle_centre_x(self) -> int:
        """Where the handle sits, kept fully inside the widget so it never
        looks half-eaten at either end."""
        radius = VOLUME_HANDLE_D // 2
        return min(
            max(int(self._volume * self.width()), radius), self.width() - radius
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        top = (h - VOLUME_TRACK_H) // 2

        painter.fillRect(0, top, w, VOLUME_TRACK_H, VOLUME_TRACK)
        fill = int(self._volume * w)
        if fill > 0:
            painter.fillRect(
                0, top, fill, VOLUME_TRACK_H,
                VOLUME_FILL_ACTIVE if self._active else VOLUME_FILL,
            )
        painter.setPen(QPen(VOLUME_TRACK_EDGE, 1))
        painter.drawRect(0, top, w - 1, VOLUME_TRACK_H - 1)

        radius = VOLUME_HANDLE_D // 2
        centre = self.handle_centre_x()
        painter.setPen(QPen(VOLUME_HANDLE_EDGE, 1))
        painter.setBrush(VOLUME_HANDLE_ACTIVE if self._active else VOLUME_HANDLE)
        painter.drawEllipse(
            QPoint(centre, h // 2), radius - 1, radius - 1
        )


class MetronomeBar(QWidget):
    """Drives a MetronomeService; owns no timing of its own."""

    bpm_changed = Signal(float)
    failed = Signal(str)

    def __init__(self, service: MetronomeService, parent=None):
        super().__init__(parent)
        self._service = service

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 0, 6, 2)
        row.setSpacing(4)

        self._play = self._button(PLAY_TEXT, self._toggle, width=28)
        self._play.setToolTip("메트로놈 재생/정지")
        row.addWidget(self._play)

        # same size, right beside it: two things you press, not a label
        self._sound = MenuButton()
        self._sound.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._sound.setFixedWidth(28)
        self._sound.clicked.connect(self._ask_sound)
        row.addWidget(self._sound)

        row.addStretch(1)
        self._down = self._button("-", lambda: self._nudge(-BPM_STEP), width=24)
        row.addWidget(self._down)

        # the tempo is a button, not a label: it is the thing you press to
        # type a new one, and it has to look pressable
        self._bpm = self._button("", self._ask_bpm, width=52)
        self._bpm.setToolTip(f"눌러서 BPM 입력 ({MIN_BPM:g}-{MAX_BPM:g})")
        row.addWidget(self._bpm)

        self._up = self._button("+", lambda: self._nudge(BPM_STEP), width=24)
        row.addWidget(self._up)

        row.addSpacing(6)
        self._volume = VolumeBar(service.volume)
        self._volume.volume_changed.connect(self._service.set_volume)
        row.addWidget(self._volume, alignment=Qt.AlignmentFlag.AlignVCenter)

        row.addStretch(1)

        self._refresh()

    def _button(self, text: str, on_click, width: int) -> QPushButton:
        button = QPushButton(text)
        # keyboard focus belongs to the window (Ctrl+R / Ctrl+L), never to a
        # tempo button that would then swallow the space bar
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedWidth(width)
        button.clicked.connect(on_click)
        return button

    def _refresh(self) -> None:
        self._bpm.setText(f"{self._service.bpm:g}")
        self._sound.setToolTip(f"메트로놈 소리: {self._service.sound}")
        self._play.setText(STOP_TEXT if self._service.running else PLAY_TEXT)

    def _toggle(self) -> None:
        try:
            self._service.toggle()
        except (RuntimeError, OSError) as error:
            # a machine with no output device is ordinary; the tuner half of
            # the window has to keep working
            self._service.stop()
            self.failed.emit(str(error))
        self._refresh()

    def _nudge(self, delta: float) -> None:
        self.set_bpm(self._service.bpm + delta)

    def _ask_bpm(self) -> None:
        value, accepted = QInputDialog.getInt(
            self,
            "메트로놈",
            f"BPM ({MIN_BPM:g}-{MAX_BPM:g})",
            int(self._service.bpm),
            int(MIN_BPM),
            int(MAX_BPM),
        )
        if accepted:
            self.set_bpm(float(value))

    def _ask_sound(self) -> None:
        dialog = SoundDialog(self._service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._service.set_sound(dialog.chosen)
        self._refresh()

    def set_sound(self, name: str) -> None:
        """Single path for the sound, so the button shows what will play."""
        self._service.set_sound(name)
        self._refresh()

    def set_volume(self, volume: float) -> None:
        """Single path for volume, so the bar shows what is actually playing —
        the service clamps and this follows the result."""
        self._volume.set_volume(self._service.set_volume(volume))

    def set_bpm(self, bpm: float) -> None:
        """Single path for every tempo change, so the button always shows what
        is actually playing — the service clamps, and this shows the result."""
        taken = self._service.set_bpm(bpm)
        self._refresh()
        self.bpm_changed.emit(taken)

    def stop(self) -> None:
        """Close the device without pretending the button was pressed."""
        self._service.stop()
        self._refresh()
