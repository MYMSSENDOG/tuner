"""The window that opens when a take stops: name it, or throw it away.

It opens with the title already filled in and selected, so Enter alone is a
valid answer — the date and time is a real name, and most takes never need a
better one. Typing replaces it, because a player who means to name this take
should not have to clear a field first.

Discarding asks. Everything else here is reversible; a performance is not.

Built to the same recipe as the metronome's sound dialog (a QDialog with a
button box), because a second dialog that behaved differently would be a
second thing to learn.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

DIALOG_STYLE = "background-color: #3b4252; color: #c7d2e3;"
FIELD_STYLE = (
    "background-color: #2a303c; color: #ffffff;"
    " border: 1px solid #5b6a85; padding: 4px 6px;"
)
META_STYLE = "color: #55627a;"
SAVE_STYLE = "background-color: #5a7a22; border: 1px solid #7cb518; color: #eaf6d6; padding: 3px 12px;"
DISCARD_STYLE = "background-color: none; border: 1px solid #454f61; color: #8b97a8; padding: 3px 10px;"

SAVE_TEXT = "저장"
DISCARD_TEXT = "버리기"


def duration_text(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


class SaveTakeDialog(QDialog):
    """Returns Accepted with `title`, or Rejected to throw the take away."""

    def __init__(self, default_title: str, seconds: float, a4_hz: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("녹음 저장")
        self.setStyleSheet(DIALOG_STYLE)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(7)

        layout.addWidget(QLabel("제목"))

        self._field = QLineEdit(default_title)
        self._field.setStyleSheet(FIELD_STYLE)
        self._field.selectAll()  # Enter keeps it, a keystroke replaces it
        layout.addWidget(self._field)

        meta = QLabel(f"길이 {duration_text(seconds)}    A4 {a4_hz:.0f}Hz")
        meta.setStyleSheet(META_STYLE)
        layout.addWidget(meta)

        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        buttons.addStretch(1)
        self._discard = QPushButton(DISCARD_TEXT)
        self._discard.setStyleSheet(DISCARD_STYLE)
        self._discard.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._discard.clicked.connect(self._confirm_discard)
        buttons.addWidget(self._discard)

        self._save = QPushButton(SAVE_TEXT)
        self._save.setStyleSheet(SAVE_STYLE)
        self._save.setDefault(True)  # Enter from the field lands here
        self._save.clicked.connect(self.accept)
        buttons.addWidget(self._save)
        layout.addLayout(buttons)

        self._field.setFocus()
        self._field.returnPressed.connect(self.accept)
        self._field.textChanged.connect(self._sync_save_enabled)
        self._sync_save_enabled(self._field.text())

    @property
    def title(self) -> str:
        return self._field.text().strip()

    def _sync_save_enabled(self, text: str) -> None:
        """An empty title has no name to save under, so the button waits."""
        self._save.setEnabled(bool(text.strip()))

    def _confirm_discard(self) -> None:
        if self._ask_discard():
            self.reject()

    def _ask_discard(self) -> bool:
        answer = QMessageBox.question(
            self,
            "녹음 버리기",
            "이 녹음을 버립니다. 되돌릴 수 없습니다.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def reject(self) -> None:
        """Esc comes through here too, which is why the question is asked in
        `keyPressEvent` rather than in this override — a programmatic reject
        (the discard button, already confirmed) must not ask twice."""
        super().reject()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._ask_discard():
                super().reject()
            return
        super().keyPressEvent(event)
