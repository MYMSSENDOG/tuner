"""The panel that slides out to the right: what is being recorded, and what
was recorded before.

Why one drawer and not two panels: recording controls and the list of takes
would otherwise be two pieces of window state to open, close and remember.
Folded together there is one open/closed, and only two ways to open it — the
record button starts a take and opens it, the handle opens it to browse
(docs/recording.md, 결정 1+3).

It hangs off the right edge so the tuner itself never moves. Every pixel in
the main column belongs to the meter; this panel takes none of them.

This module is the view only. It owns no audio: it is told the elapsed time
and the input level, and it emits what the user pressed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

WIDTH = 200  # enough for a title like "바흐 무반주 1번 프렐류드" and a duration
SCROLLBAR_ROOM = 16  # the vertical bar appears once the list fills; titles allow for it

PANEL_BG = "#343b49"
RECORDING_BG = "#4a3238"
RECORDING_EDGE = "#5d4149"
LIST_BG = "#2d343f"
DIM = "#55627a"

# The level history drawn while recording: one bar per reading, oldest left.
# Not a waveform of the file - it is the same input level the bar under the
# meter shows, kept just long enough to prove the microphone is still live.
WAVE_BARS = 34
WAVE_HEIGHT = 22
WAVE_BAR = QColor("#b05b5b")
WAVE_FLOOR = QColor("#6e4a4e")  # bars from while it was paused
WAVE_BG = QColor("#3d2b30")

PAUSE_TEXT = "❚❚ 일시정지"
RESUME_TEXT = "▶ 재개"


def elapsed_text(seconds: float) -> str:
    """mm:ss, and it keeps counting past an hour rather than wrapping."""
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


class _LevelTrail(QWidget):
    """The strip of level bars beside the elapsed time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars: list[tuple[float, bool]] = []  # (0..1 height, live)
        self.setFixedHeight(WAVE_HEIGHT)

    def push(self, level: float, live: bool = True) -> None:
        self._bars.append((min(1.0, max(0.0, level)), live))
        del self._bars[:-WAVE_BARS]
        self.update()

    def clear(self) -> None:
        self._bars = []
        self.update()

    @property
    def bars(self) -> list[tuple[float, bool]]:
        return list(self._bars)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), WAVE_BG)
        if not self._bars:
            return
        step = self.width() / WAVE_BARS
        for i, (level, live) in enumerate(self._bars):
            height = max(1, int(level * (self.height() - 2)))
            painter.fillRect(
                int(i * step),
                (self.height() - height) // 2,
                max(1, int(step) - 1),
                height,
                WAVE_BAR if live else WAVE_FLOOR,
            )


class RecordingsDrawer(QWidget):
    """Recording controls on top (only while recording), the list below."""

    pause_pressed = Signal()
    stop_pressed = Signal()
    play_pressed = Signal()
    rename_pressed = Signal()
    delete_pressed = Signal()
    folder_pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(WIDTH)
        self.setStyleSheet(
            f"background-color: {PANEL_BG}; color: #c7d2e3;"
            " border-left: 1px solid #4a5569;"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- recording panel: present only while a take is running ---
        self._recording_panel = QWidget()
        self._recording_panel.setStyleSheet(
            f"background-color: {RECORDING_BG};"
            f" border-top: 0; border-bottom: 1px solid {RECORDING_EDGE};"
            " border-left: 0;"
        )
        panel = QVBoxLayout(self._recording_panel)
        panel.setContentsMargins(7, 6, 7, 6)
        panel.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)
        self._elapsed = QLabel("0:00")
        self._elapsed.setStyleSheet(
            "color: #ffd9d9; font-weight: 600; border: 0; background: transparent;"
        )
        top.addWidget(self._elapsed)
        top.addStretch(1)
        self._state_note = QLabel("")
        self._state_note.setStyleSheet(
            "color: #d0a9a9; border: 0; background: transparent;"
        )
        top.addWidget(self._state_note)
        panel.addLayout(top)

        self._trail = _LevelTrail()
        panel.addWidget(self._trail)

        actions = QHBoxLayout()
        actions.setSpacing(5)
        self._pause_button = self._action(PAUSE_TEXT, self.pause_pressed, "#5e3c42", "#7d4f56")
        self._stop_button = self._action("■ 중지", self.stop_pressed, "#7a3b3b", "#a35151")
        actions.addWidget(self._pause_button, 1)
        actions.addWidget(self._stop_button, 1)
        panel.addLayout(actions)
        self._recording_panel.hide()
        outer.addWidget(self._recording_panel)

        # --- the list of takes ---
        self._heading = QLabel("녹음 0개")
        self._heading.setStyleSheet(f"color: {DIM}; padding: 6px 8px 3px; border: 0;")
        outer.addWidget(self._heading)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 0, 6, 4)
        toolbar.setSpacing(4)
        for text, signal in (
            ("▶", self.play_pressed),
            ("이름", self.rename_pressed),
            ("삭제", self.delete_pressed),
        ):
            toolbar.addWidget(self._tool(text, signal))
        toolbar.addStretch(1)
        toolbar.addWidget(self._tool("폴더", self.folder_pressed))
        outer.addLayout(toolbar)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background-color: {LIST_BG}; border: 1px solid #434e63;"
            " border-left: 0; border-right: 0; }"
            " QListWidget::item { padding: 3px 5px; }"
            " QListWidget::item:selected { background-color: #45536b; }"
        )
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # A 200px panel has no room to scroll sideways: long titles are elided
        # (set_takes) rather than hidden behind a bar nobody would drag.
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # And it must be allowed to shrink. Left at its own minimum the list
        # alone asks for 70px, which pushed the whole window 10px taller the
        # moment the recording panel appeared — the one thing the drawer is
        # built not to do.
        self._list.setMinimumHeight(0)
        self._list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        outer.addWidget(self._list, 1)

        self._footer = QLabel("아직 녹음이 없습니다")
        self._footer.setStyleSheet(f"color: {DIM}; padding: 4px 8px; border: 0;")
        self._footer.setWordWrap(True)
        outer.addWidget(self._footer)

    # --- small builders -------------------------------------------------

    def _action(self, text: str, signal, background: str, border: str) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setStyleSheet(
            f"background-color: {background}; border: 1px solid {border};"
            " color: #ffd9d9; padding: 2px 0;"
        )
        button.clicked.connect(signal.emit)
        return button

    def _tool(self, text: str, signal) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setStyleSheet(
            "background-color: #3f4a5e; border: 1px solid #4f5b70;"
            " color: #c7d2e3; padding: 1px 5px;"
        )
        button.clicked.connect(signal.emit)
        return button

    # --- what the window tells it ---------------------------------------

    def show_recording(self, running: bool) -> None:
        self._recording_panel.setVisible(running)
        if not running:
            self._trail.clear()
            self._elapsed.setText("0:00")
            self._state_note.setText("")

    def set_paused(self, paused: bool) -> None:
        self._pause_button.setText(RESUME_TEXT if paused else PAUSE_TEXT)
        self._state_note.setText("일시정지" if paused else "")

    def set_elapsed(self, seconds: float) -> None:
        self._elapsed.setText(elapsed_text(seconds))

    def push_level(self, level: float, live: bool = True) -> None:
        self._trail.push(level, live)

    def set_takes(self, takes: list[tuple[str, str]], keep: int | None = None) -> None:
        """(title, duration) newest first — that is the order they are read in.

        The duration is right-aligned and never elided: it is three or four
        characters and it is what distinguishes two takes made a minute apart.
        The title gives up its tail instead.
        """
        self._list.clear()
        for title, duration in takes:
            item = QListWidgetItem(self._list)
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(5, 2, 5, 2)
            layout.setSpacing(6)

            name = QLabel()
            name.setStyleSheet("border: 0; background: transparent;")
            metrics = QFontMetrics(name.font())
            room = WIDTH - 2 * 5 - 6 - metrics.horizontalAdvance(duration) - SCROLLBAR_ROOM
            name.setText(metrics.elidedText(title, Qt.TextElideMode.ElideRight, room))
            name.setToolTip(title)
            layout.addWidget(name, 1)

            length = QLabel(duration)
            length.setStyleSheet(f"color: {DIM}; border: 0; background: transparent;")
            layout.addWidget(length)

            item.setSizeHint(row.sizeHint())
            self._list.setItemWidget(item, row)
        self._heading.setText(f"녹음 {len(takes)}개")
        if takes:
            # Something is always selected, so "재생" and "삭제" always have a
            # subject — a toolbar whose buttons do nothing until you click a
            # row first reads as broken.
            self._list.setCurrentRow(min(keep if keep is not None else 0, len(takes) - 1))

    def set_footer(self, text: str) -> None:
        self._footer.setText(text)

    # --- for tests and the window ---------------------------------------

    @property
    def recording_visible(self) -> bool:
        return self._recording_panel.isVisible()

    @property
    def elapsed_text(self) -> str:
        return self._elapsed.text()

    @property
    def pause_text(self) -> str:
        return self._pause_button.text()

    @property
    def take_count(self) -> int:
        return self._list.count()

    @property
    def selected_index(self) -> int:
        """Which take the toolbar acts on, or -1 when the list is empty."""
        return self._list.currentRow()

    def select(self, index: int) -> None:
        if 0 <= index < self._list.count():
            self._list.setCurrentRow(index)
