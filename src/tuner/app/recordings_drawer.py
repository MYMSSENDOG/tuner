"""The recorder panel: a list of takes, a take being made, a take being heard.

Built to the shape of the Windows Voice Recorder, which is the recorder these
users already know — one big round control in the middle, a list grouped by
when things were recorded, a playback page you reach by opening a take and
leave by a back arrow. Only the colours are this app's: the panel is the same
slate as the meter, and the record circle is the red the needle already uses
rather than Windows' blue, because red is what a record button is.

Three pages, one at a time (`QStackedWidget`), because that is how the tuner
column stays 310px wide with a drawer beside it instead of growing a region
per state.

The view owns no audio and no files. It is told what to show, and it says what
was pressed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tuner.app.context_menu import modern_menu
from tuner.app.round_button import MIC, PAUSE, PLAY, RING_SPAN, STOP, RoundButton

WIDTH = 280  # a title, a date and a duration on one line, with room to read

PANEL_BG = "#343b49"
ROW_HOVER = "#3d4555"
ROW_SELECTED = "#45536b"
DIVIDER = "#434e63"
INK = "#c7d2e3"
DIM = "#8793a6"
FAINT = "#5c6878"

RECORD_FILL = "#c5443f"  # the needle's red, dimmed to sit on a dark panel
RECORD_GLYPH = "#ffffff"
PLAY_FILL = "#4d5f7d"
PLAY_GLYPH = "#dce6f5"

# The mic sits under a list that wants every pixel, so it is the smallest of
# the three: big enough to aim at without looking, no bigger. Stop and play own
# their pages, and the stop button also wears rings while a take is live.
MIC_DIAMETER = 34
STOP_DIAMETER = 56
PLAY_DIAMETER = 56

# The level history drawn while recording: one bar per reading, oldest left.
# Not the file's waveform — it is the same input level the bar under the meter
# shows, kept just long enough to prove the microphone is still live.
WAVE_BARS = 44
WAVE_HEIGHT = 26
WAVE_BAR = QColor("#b4625d")
WAVE_FLOOR = QColor("#6d4f4d")  # bars from while it was paused
WAVE_BG = QColor("#2f3542")

STATUS_OK = "#9fc45a"
STATUS_BAD = "#e08a8a"


def clock_text(seconds: float) -> str:
    """hh:mm:ss, the way a recorder counts: always three fields, so the digits
    do not jump sideways when a take passes a minute."""
    whole = max(int(seconds), 0)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def short_clock(seconds: float) -> str:
    """m:ss for the seek bar's ends, where three fields would be noise."""
    whole = max(int(seconds), 0)
    return f"{whole // 60}:{whole % 60:02d}"


def elapsed_text(seconds: float) -> str:
    """The list's duration column, and what the window's tests read."""
    return short_clock(seconds)


class _LevelTrail(QWidget):
    """The strip of level bars under the recording clock."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars: list[tuple[float, bool]] = []
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
            height = max(1, int(level * (self.height() - 4)))
            painter.fillRect(
                int(i * step),
                (self.height() - height) // 2,
                max(1, int(step) - 1),
                height,
                WAVE_BAR if live else WAVE_FLOOR,
            )


class _TakeRow(QPushButton):
    """One take in the list: title, when it was made, how long it runs."""

    def __init__(self, title: str, when: str, duration: str, parent=None):
        super().__init__(parent)
        self.setFlat(True)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton { background: transparent; border: 0;"
            f" border-bottom: 1px solid {DIVIDER}; text-align: left; padding: 0; }}"
            f" QPushButton:hover {{ background-color: {ROW_HOVER}; }}"
            f" QPushButton:checked {{ background-color: {ROW_SELECTED}; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 7, 12, 7)
        row.setSpacing(8)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        self._title = QLabel()
        self._title.setStyleSheet(f"color: {INK}; background: transparent; border: 0;")
        self._when = QLabel(when)
        self._when.setStyleSheet(f"color: {DIM}; background: transparent; border: 0;")
        texts.addWidget(self._title)
        texts.addWidget(self._when)
        row.addLayout(texts, 1)

        length = QLabel(duration)
        length.setStyleSheet(f"color: {DIM}; background: transparent; border: 0;")
        row.addWidget(length, 0, Qt.AlignmentFlag.AlignVCenter)

        # elided once: the drawer has a fixed width, so the room never changes
        metrics = QFontMetrics(self._title.font())
        room = WIDTH - 24 - 8 - metrics.horizontalAdvance(duration) - 18
        self._title.setText(metrics.elidedText(title, Qt.TextElideMode.ElideRight, room))
        self.setToolTip(title)
        # A QPushButton sizes itself from its own text and never consults a
        # layout placed inside it - left alone these rows came out 13px tall
        # and the two lines of text were simply clipped.
        self.setMinimumHeight(row.sizeHint().height())

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()


class RecordingsDrawer(QWidget):
    """List / recording / playback, one page at a time."""

    record_pressed = Signal()
    open_pressed = Signal(int)
    pause_pressed = Signal()
    preview_pressed = Signal()  # hear the paused take so far
    stop_pressed = Signal()
    back_pressed = Signal()
    play_pressed = Signal()
    seeked = Signal(float)
    rename_pressed = Signal()
    delete_pressed = Signal()
    folder_pressed = Signal()

    LIST, RECORDING, PLAYBACK = 0, 1, 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(WIDTH)
        self.setStyleSheet(
            f"background-color: {PANEL_BG}; color: {INK}; border-left: 1px solid #4a5569;"
        )
        self._rows: list[_TakeRow] = []
        self._selected = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_list_page())
        self._pages.addWidget(self._build_recording_page())
        self._pages.addWidget(self._build_playback_page())
        outer.addWidget(self._pages, 1)

        # Everything this feature has to say appears here, under whichever page
        # is showing — the tuner's own note line stays about the tuner.
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.hide()
        self.say("")
        outer.addWidget(self._status)

    # --- pages ------------------------------------------------------------

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("background: transparent; border: 0;")
        # must stay free to shrink: left at its own minimum it would set the
        # window's height floor instead of the meter doing so
        self._scroll.setMinimumHeight(0)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)

        self._list_body = QWidget()
        self._list_layout = QVBoxLayout(self._list_body)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_body)
        layout.addWidget(self._scroll, 1)

        self._empty = QLabel("아직 녹음이 없습니다\n아래 버튼으로 시작하세요")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(f"color: {FAINT}; border: 0; padding: 18px;")
        layout.addWidget(self._empty, 1)

        tray = QHBoxLayout()
        tray.setContentsMargins(0, 6, 0, 8)
        self._mic = RoundButton(MIC, MIC_DIAMETER, RECORD_FILL, RECORD_GLYPH)
        self._mic.setToolTip("녹음 시작 (Alt+R)")
        self._mic.clicked.connect(self.record_pressed.emit)
        tray.addStretch(1)
        tray.addWidget(self._mic)
        tray.addStretch(1)
        layout.addLayout(tray)
        return page

    def _build_recording_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 8, 12, 0)
        layout.setSpacing(6)
        layout.addStretch(1)

        self._clock = QLabel(clock_text(0))
        self._clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._clock.setStyleSheet(f"color: {INK}; border: 0; font-size: 24px; font-weight: 300;")
        layout.addWidget(self._clock)

        self._trail = _LevelTrail()
        layout.addWidget(self._trail)

        tray = QHBoxLayout()
        self._stop = RoundButton(
            STOP, STOP_DIAMETER, RECORD_FILL, RECORD_GLYPH, ring_span=RING_SPAN
        )
        self._stop.setToolTip("중지하고 저장 (Alt+S)")
        self._stop.set_ringed(True)
        self._stop.clicked.connect(self.stop_pressed.emit)
        tray.addStretch(1)
        tray.addWidget(self._stop)
        tray.addStretch(1)
        layout.addLayout(tray)

        under = QHBoxLayout()
        self._pause = self._flat("❚❚", "일시정지 (Alt+P)", self.pause_pressed)
        # Only while paused: hearing the take back while it is still being
        # recorded would put the playback into the take.
        self._preview = self._flat("▶ 지금까지 듣기", "일시정지한 곳까지 들어보기", self.preview_pressed)
        self._preview.hide()
        under.addStretch(1)
        under.addWidget(self._pause)
        under.addWidget(self._preview)
        under.addStretch(1)
        layout.addLayout(under)
        layout.addStretch(1)
        return page

    def _build_playback_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 6, 12, 2)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(4)
        self._back = self._flat("←", "목록으로", self.back_pressed)
        head.addWidget(self._back, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self._play_title = QLabel("")
        self._play_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._play_title.setStyleSheet(f"color: {INK}; border: 0;")
        self._play_when = QLabel("")
        self._play_when.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._play_when.setStyleSheet(f"color: {DIM}; border: 0;")
        titles.addWidget(self._play_title)
        titles.addWidget(self._play_when)
        head.addLayout(titles, 1)
        head.addSpacing(self._back.sizeHint().width())  # keeps the title centred
        layout.addLayout(head)

        layout.addStretch(1)
        tray = QHBoxLayout()
        self._play = RoundButton(PLAY, PLAY_DIAMETER, PLAY_FILL, PLAY_GLYPH)
        self._play.clicked.connect(self.play_pressed.emit)
        tray.addStretch(1)
        tray.addWidget(self._play)
        tray.addStretch(1)
        layout.addLayout(tray)
        layout.addStretch(1)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(6)
        self._at = QLabel("0:00")
        self._at.setStyleSheet(f"color: {DIM}; border: 0;")
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 1000)
        self._seek.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._seek.setStyleSheet(
            "QSlider { border: 0; }"
            f" QSlider::groove:horizontal {{ height: 3px; background: {FAINT};"
            " border: 0; border-radius: 1px; }"
            f" QSlider::sub-page:horizontal {{ background: {PLAY_GLYPH}; border-radius: 1px; }}"
            f" QSlider::handle:horizontal {{ background: {PLAY_GLYPH}; width: 11px;"
            " height: 11px; margin: -4px 0; border-radius: 5px; }"
        )
        self._seek.sliderMoved.connect(self._on_seek)
        self._total = QLabel("0:00")
        self._total.setStyleSheet(f"color: {DIM}; border: 0;")
        seek_row.addWidget(self._at)
        seek_row.addWidget(self._seek, 1)
        seek_row.addWidget(self._total)
        layout.addLayout(seek_row)

        tools = QHBoxLayout()
        tools.setSpacing(2)
        tools.addStretch(1)
        for text, tip, signal in (
            ("이름", "이름 바꾸기", self.rename_pressed),
            ("삭제", "이 녹음을 지웁니다", self.delete_pressed),
            ("폴더", "저장 폴더 열기", self.folder_pressed),
        ):
            tools.addWidget(self._flat(text, tip, signal))
        tools.addStretch(1)
        layout.addLayout(tools)
        return page

    def _flat(self, text: str, tip: str, signal) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tip)
        button.setFlat(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 0; color: {DIM};"
            " padding: 3px 8px; }"
            f" QPushButton:hover {{ color: {INK}; }}"
        )
        button.clicked.connect(signal.emit)
        return button

    # --- what the window tells it -----------------------------------------

    @property
    def page(self) -> int:
        return self._pages.currentIndex()

    def show_list(self) -> None:
        self._pages.setCurrentIndex(self.LIST)

    def show_recording(self) -> None:
        self._trail.clear()
        self.set_elapsed(0.0)
        self.set_paused(False)
        self._pages.setCurrentIndex(self.RECORDING)

    def show_playback(self, title: str, when: str, seconds: float) -> None:
        self._play_title.setText(title)
        self._play_when.setText(when)
        self._total.setText(short_clock(seconds))
        self.set_position(0.0, seconds)
        self.set_playing(False)
        self._pages.setCurrentIndex(self.PLAYBACK)

    def set_takes(self, takes: list[tuple[str, str, str, str]], keep: int = 0) -> None:
        """(group heading or "", title, when, duration), newest first."""
        self._rows = []
        while self._list_layout.count() > 1:  # keep the trailing stretch
            item = self._list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        for index, (heading, title, when, duration) in enumerate(takes):
            if heading:
                label = QLabel(heading)
                label.setStyleSheet(f"color: {DIM}; border: 0; padding: 9px 12px 3px;")
                self._list_layout.insertWidget(self._list_layout.count() - 1, label)
            row = _TakeRow(title, when, duration)
            row.clicked.connect(lambda _=False, i=index: self._on_row(i))
            row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            row.customContextMenuRequested.connect(
                lambda pos, i=index, r=row: self._show_row_menu(i, r.mapToGlobal(pos))
            )
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._rows.append(row)

        self._empty.setVisible(not takes)
        self._scroll.setVisible(bool(takes))
        self.select(min(keep, len(self._rows) - 1) if self._rows else -1)

    def select(self, index: int) -> None:
        self._selected = index
        for i, row in enumerate(self._rows):
            row.setChecked(i == index)

    def set_paused(self, paused: bool) -> None:
        self._pause.setText("▶" if paused else "❚❚")
        self._pause.setToolTip("재개 (Alt+P)" if paused else "일시정지 (Alt+P)")
        self._stop.set_ringed(not paused)
        self._preview.setVisible(paused)
        if not paused:
            self.set_previewing(False)

    def set_previewing(self, previewing: bool) -> None:
        self._preview.setText("■ 그만 듣기" if previewing else "▶ 지금까지 듣기")

    @property
    def preview_text(self) -> str:
        return self._preview.text() if self._preview.isVisibleTo(self) else ""

    def set_elapsed(self, seconds: float) -> None:
        self._clock.setText(clock_text(seconds))

    def push_level(self, level: float, live: bool = True) -> None:
        self._trail.push(level, live)

    def set_playing(self, playing: bool) -> None:
        self._play.set_glyph(PAUSE if playing else PLAY)

    def set_position(self, seconds: float, total: float) -> None:
        self._at.setText(short_clock(seconds))
        if self._seek.isSliderDown():
            return  # do not fight a finger that is dragging it
        self._seek.setValue(int(1000 * seconds / total) if total > 0 else 0)

    def say(self, text: str, ok: bool = True) -> None:
        self._status.setStyleSheet(
            f"color: {STATUS_OK if ok else STATUS_BAD}; border: 0;"
            f" border-top: 1px solid {DIVIDER}; padding: 4px 10px;"
        )
        self._status.setText(text)
        self._status.setVisible(bool(text))

    def _on_row(self, index: int) -> None:
        self.select(index)
        self.open_pressed.emit(index)

    def row_menu(self, index: int) -> QMenu:
        """The right-click menu for one take.

        Built separately from showing it so that what it offers can be checked
        without opening a popup. The row is selected first: every action here
        acts on the selected take, exactly as the playback page's buttons do,
        so the menu and the page cannot disagree about which take they mean.
        """
        self.select(index)
        menu = modern_menu(self)
        menu.addAction("재생", lambda: self.open_pressed.emit(index))
        menu.addAction("이름 바꾸기", self.rename_pressed.emit)
        menu.addAction("폴더 열기", self.folder_pressed.emit)
        menu.addSeparator()  # the one action that cannot be undone stands apart
        menu.addAction("삭제", self.delete_pressed.emit)
        return menu

    def _show_row_menu(self, index: int, at) -> None:
        self.row_menu(index).exec(at)

    def _on_seek(self, value: int) -> None:
        self.seeked.emit(value / 1000.0)

    # --- for the window and the tests ---------------------------------------

    @property
    def selected_index(self) -> int:
        return self._selected

    @property
    def take_count(self) -> int:
        return len(self._rows)

    @property
    def elapsed_text(self) -> str:
        return self._clock.text()

    @property
    def pause_text(self) -> str:
        return self._pause.text()

    @property
    def status_text(self) -> str:
        return self._status.text()

    @property
    def recording_visible(self) -> bool:
        return self.page == self.RECORDING

    @property
    def play_glyph(self) -> str:
        return self._play.glyph
