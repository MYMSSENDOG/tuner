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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QPushButton, QWidget

from tuner.app.metronome import MetronomeService
from tuner.core.metronome import MAX_BPM, MIN_BPM

PLAY_TEXT = "▶"
STOP_TEXT = "■"
BPM_STEP = 1.0


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
