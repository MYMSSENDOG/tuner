"""Main window: meter + A4 reference, input device selection, always-on-top."""

from __future__ import annotations

import signal
import threading
from pathlib import Path

import soundfile as sf
from PySide6.QtCore import (
    QEvent,
    QObject,
    QSettings,
    QSignalBlocker,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tuner.app import recorder, take_analysis
from tuner.app.beat_lane import BeatLane
from tuner.app.capture import MAX_SESSION_SECONDS, RING_SECONDS, FieldCapture
from tuner.app.drawer_handle import DrawerHandle
from tuner.app.engine import DIGITAL_SILENCE_DBFS, TunerEngine, TunerReading
from tuner.app.level_widget import InputLevelBar
from tuner.app.level_widget import fill_fraction as level_fraction
from tuner.app.meter_widget import MeterWidget
from tuner.app.metronome import MetronomeService
from tuner.app.metronome_widget import MetronomeBar
from tuner.app.recordings_drawer import WIDTH as DRAWER_WIDTH
from tuner.app.recordings_drawer import RecordingsDrawer
from tuner.app.save_take_dialog import SaveTakeDialog
from tuner.app.take_analysis import PitchTrack
from tuner.app.take_player import TakePlayer
from tuner.app.trace_widget import PitchTraceWidget
from tuner.audio.input import AudioInput
from tuner.audio.output import AudioOutput
from tuner.audio.sounddevice_output import SoundDeviceOutput
from tuner.core.metronome import (
    CLICK_AMPLITUDE,
    DEFAULT_BEATS_PER_BAR,
    DEFAULT_BPM,
    DEFAULT_SOUND,
)

# Bumped when the default window size changes: a stored geometry from the
# tall layout would otherwise survive the update and re-stretch the window,
# so the old key is simply left behind and the new default applies once.
# v6: the detector combo left the controls row, which was the window's width
# floor - 354px measured, above the 310 this asks for, so the window had been
# silently wider than its own default. Without it the floor is 294 and the
# default is reachable, but only for someone who is not carrying a v5
# geometry.
# v7: the recorder arrived. It briefly put a button on the controls row and a
# handle in the layout, which took the window to 331; both were taken back out
# (the drawer owns the recorder's controls, and the handle is overlaid), so the
# floor is what it always was. The key stays bumped: a v6 geometry carried
# through the wide version would restore the wide size.
GEOMETRY_KEY = "geometry_v7"

# What the tuner column itself is, unchanged since the meter was drawn: the
# controls row sets a 294px floor and this is the room above it. The drawer
# and its handle are added beside this, never taken out of it.
TUNER_WIDTH = 310

# The two report keys. Ctrl is the *report* family (developer-facing: audio
# plus what the meter showed, for turning a defect into a fixture); the
# player-facing recorder gets its own modifier so the two never look alike.
#
# One key, no menu: the moment worth reporting has already passed when the
# player reaches for the mouse. Undiscoverable on purpose - the UI stays a
# meter (README documents it), and nothing about it costs anything until used.
REPORT_SHORTCUT = "Ctrl+R"
# Ctrl+R is for the moment already gone. This is the other half: sit down,
# press it, play the thing that misbehaves, press it again. Nothing is
# dropped in between, so the whole episode is on disk in one file.
# Was Ctrl+L. Moved so that this stays visibly part of the Ctrl report family
# and cannot be mistaken for the recorder's own keys.
REPORT_SESSION_SHORTCUT = "Ctrl+Shift+R"
# === UI switch: set True to put the session-report button back on the
# === controls row. Off by default - the row is the window's width floor,
# === and the capture layer underneath it stays live either way (Ctrl+R,
# === FieldCapture, tools/promote). Flip this for a logging session.
REPORT_SESSION_BUTTON_ENABLED = False


def enable_ctrl_c(window: QMainWindow) -> QTimer:
    """Make Ctrl+C close the window (and thus quit cleanly via closeEvent).

    Python's SIGINT handler only runs while the interpreter executes, and
    Qt's event loop blocks in C++ — the returned timer periodically yields
    control so the handler gets a chance. Keep a reference to the timer.
    """
    signal.signal(signal.SIGINT, lambda *_: window.close())
    timer = QTimer(window)
    timer.timeout.connect(lambda: None)
    timer.start(200)
    return timer


SESSION_IDLE_TEXT = "● 리포트"
SESSION_ACTIVE_TEXT = "■ 리포트 중 {}"

# --- the player-facing recorder ---------------------------------------------
# Alt, not Ctrl: the modifier is what tells this apart from the report keys
# above, and it keeps the two families from ever being confused for each other
# (docs/recording.md 결정 7). Three actions, two keys each, all reachable with
# one hand while the other is holding an instrument.
RECORD_SHORTCUT = "Alt+R"
PAUSE_SHORTCUT = "Alt+P"
STOP_SHORTCUT = "Alt+S"
DRAWER_SHORTCUT = "Alt+L"

# The recorder puts nothing on the tuner's own row. Its controls live in the
# drawer, the way a voice recorder's do — one big circle in the middle of its
# own panel. The tuner row is therefore exactly what it was before this
# feature, and the window's width floor with it.
# Below this a take is two presses in a row, not a performance. Short enough
# that a scale fragment still counts.
MIN_TAKE_SECONDS = 0.5
RECORDER_TICK_MS = 250  # the elapsed clock; finer than a second so it looks live
PLAYBACK_TICK_MS = 100  # the seek bar: smooth enough to read as moving
# The tuner is 190 tall and a list of takes cannot live in that - at 197 the
# panel showed one and a half rows behind a scrollbar. Opening the drawer
# therefore also gives the window the height a list needs, and closing it
# hands that height back, so the tuner alone is never any taller than it was.
DRAWER_MIN_HEIGHT = 360
DRAWER_OPEN_KEY = "drawer_open"


def _cache_key(path: Path) -> tuple[str, int, int] | None:
    """A take's identity for the analysis cache: renaming changes the path and
    re-recording changes the size and time, so a stale graph cannot be served."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


class _ReadingBridge(QObject):
    """Marshals engine readings from the audio thread onto the GUI thread."""

    reading = Signal(object)
    # (generation, PitchTrack): a take's analysis, from its worker thread
    pitch_track = Signal(int, object)


class _DeviceComboBox(QComboBox):
    """Re-enumerates audio devices the moment the dropdown opens.

    PortAudio snapshots the device list at initialization and never rescans,
    so a hot-plugged USB microphone stays invisible until a re-init — which
    tears down the open stream. Doing it on popup ties the ~100ms audio gap
    to the one moment the user is explicitly about to change devices.
    """

    about_to_show = Signal()

    def showPopup(self) -> None:
        self.about_to_show.emit()
        super().showPopup()


class MainWindow(QMainWindow):
    def __init__(
        self,
        audio_input: AudioInput,
        settings: QSettings | None = None,
        audio_output: AudioOutput | None = None,
        playback_output: AudioOutput | None = None,
        recordings_dir: Path | None = None,
    ):
        super().__init__()
        self.setWindowTitle("Tuner")
        self._settings = settings if settings is not None else QSettings("tuner", "tuner")

        self._bridge = _ReadingBridge()
        self._capture = FieldCapture()
        # built before the engine: the tuner has to be handed the click
        # timeline at construction so it can look away while one passes
        # (core/interference.py)
        self._metronome = MetronomeService(audio_output or SoundDeviceOutput())
        # The take recorder is a second block sink beside the report capture;
        # neither knows about the other (app/recorder.py).
        self._recordings_dir = (
            recordings_dir if recordings_dir is not None else recorder.recordings_dir()
        )
        # Streams each take straight into the folder it will be saved in, so
        # keeping it is a rename on the same disk (app/recorder.py).
        self._recorder = recorder.Recorder(directory=self._recordings_dir)
        self._takes: list[recorder.Take] = []
        # A second output stream, opened only when something is played. Sharing
        # the metronome's would mean a click could not sound over a playback.
        self._player = TakePlayer(playback_output or SoundDeviceOutput())
        self._engine = TunerEngine(
            audio_input,
            self._bridge.reading.emit,
            capture=self._capture,
            recorder=self._recorder,
            interference=self._metronome.clicks,
        )
        self._report_shortcut = QShortcut(QKeySequence(REPORT_SHORTCUT), self)
        self._report_shortcut.activated.connect(self.save_report)

        # The recorder's keys exist whether or not the drawer is open: the
        # point of them is that a player's hands are on the instrument.
        self._recorder_shortcuts = [
            self._shortcut(RECORD_SHORTCUT, self.toggle_recording),
            self._shortcut(PAUSE_SHORTCUT, self.toggle_pause),
            self._shortcut(STOP_SHORTCUT, self.stop_recording),
            self._shortcut(DRAWER_SHORTCUT, self.toggle_drawer),
        ]
        # What the window was before the drawer stretched it, so closing can
        # put it back rather than leaving the tuner permanently taller.
        self._height_before_drawer: int | None = None
        self._recorder_timer = QTimer(self)
        self._recorder_timer.setInterval(RECORDER_TICK_MS)
        self._recorder_timer.timeout.connect(self._tick_recorder)
        # Drives the playback page's clock and seek bar, and notices the end of
        # a take - rendering runs on the audio thread and must not touch the
        # device, so the end is seen here rather than reported from there. It
        # lives exactly as long as a take is loaded: `_put_take_away` is the
        # one place that ends both.
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(PLAYBACK_TICK_MS)
        self._play_timer.timeout.connect(self._tick_playback)

        self._meter = MeterWidget()
        self._bridge.reading.connect(self._on_reading)
        self._bridge.pitch_track.connect(self._on_pitch_track)
        # Each opened take gets a generation number; an analysis still running
        # for a take that has since been closed sees the number change and
        # stops, and anything it already sent is ignored. Without it a slow
        # analysis of the last take could draw over the one now open.
        self._analysis_generation = 0
        self._pitch_cache: dict[tuple[str, int, int], PitchTrack] = {}

        controls = QHBoxLayout()
        controls.setContentsMargins(6, 4, 6, 2)
        controls.setSpacing(4)

        controls.addWidget(QLabel("A4"))
        self._a4_spin = QSpinBox()
        self._a4_spin.setRange(415, 466)
        self._a4_spin.setValue(442)
        self._a4_spin.setSuffix(" Hz")
        self._a4_spin.lineEdit().setReadOnly(True)  # up/down buttons only, 1Hz steps
        self._a4_spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._a4_spin.valueChanged.connect(lambda v: self._engine.set_a4(float(v)))
        controls.addWidget(self._a4_spin)

        controls.addSpacing(6)
        self._audio_input = audio_input
        self._device_combo = _DeviceComboBox()
        self._device_combo.setToolTip("Input device")
        # the row is the window's width floor, so nothing here reserves more
        # than it needs; the combo elides and the tooltip carries the full name
        self._device_combo.setMinimumWidth(56)
        self._device_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._populate_devices(audio_input.list_devices())
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._device_combo.about_to_show.connect(self._refresh_device_list)
        controls.addWidget(self._device_combo, stretch=1)

        self._pin_check = QCheckBox("Pin")
        self._pin_check.setToolTip("Always on top")
        self._pin_check.toggled.connect(self._on_pin_toggled)
        controls.addWidget(self._pin_check)

        self._session_button = QPushButton(SESSION_IDLE_TEXT)
        self._session_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._session_button.clicked.connect(self.toggle_report_session)
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(500)
        self._session_timer.timeout.connect(self._tick_session)
        # The key is registered either way; only the button is behind the
        # switch. They were bound together, which meant README documented a
        # shortcut that did not exist in a default build - the button costs
        # width on the window's floor row, a shortcut costs nothing.
        self._session_shortcut = self._shortcut(
            REPORT_SESSION_SHORTCUT, self.toggle_report_session
        )
        if REPORT_SESSION_BUTTON_ENABLED:
            controls.addWidget(self._session_button)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(controls)
        # under the tuner's own controls, above the meter: the metronome is a
        # second instrument on the same panel, not a mode of the first
        self._metronome_bar = MetronomeBar(self._metronome)
        self._metronome_bar.failed.connect(lambda why: self._flash(why, ok=False))
        layout.addWidget(self._metronome_bar)
        self._no_signal = QLabel(
            "입력이 완전한 무음입니다 — 마이크 권한(시스템 설정 > 개인정보 보호 > 마이크)"
            "과 입력 장치를 확인하세요."
        )
        self._no_signal.setStyleSheet(
            "background-color: #7a3b3b; color: #ffd9d9; padding: 6px;"
        )
        self._no_signal.setWordWrap(True)
        self._no_signal.hide()
        layout.addWidget(self._no_signal)
        self._note = QLabel()
        self._note.setWordWrap(True)
        self._note.hide()
        layout.addWidget(self._note)
        layout.addWidget(self._meter, stretch=1)
        self._level_bar = InputLevelBar()
        self._level_bar.gate_changed.connect(self._engine.set_input_gate_dbfs)
        self._level_bar.set_gate(self._engine.input_gate_dbfs)
        layout.addWidget(self._level_bar)
        self._trace = PitchTraceWidget()
        layout.addWidget(self._trace)
        self._silent_readings = 0

        # The tuner is the column above; the drawer hangs off its right edge so
        # that opening it moves nothing. The handle lives between them and is
        # the only part of this apparatus that is always on screen.
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(central, 1)
        # Drawn *over* the tuner's right edge rather than beside it. In the
        # layout it cost 11px of window for ever; overlaid it costs none, and
        # the strip it covers is the empty margin outside the meter's arc.
        self._tuner_column = central
        self._drawer_handle = DrawerHandle(central)
        self._drawer_handle.toggled.connect(self.toggle_drawer)
        self._drawer_handle.raise_()
        central.installEventFilter(self)
        # The visual metronome, drawn over the meter's empty left margin. It
        # follows the meter (which moves when the no-signal banner appears)
        # and lets every click through to what is underneath.
        self._beat_lane = BeatLane(self._metronome, central)
        self._meter.installEventFilter(self)
        self._metronome_bar.running_changed.connect(self._beat_lane.set_running)
        self._drawer = RecordingsDrawer()
        self._drawer.record_pressed.connect(self.toggle_recording)
        self._drawer.pause_pressed.connect(self.toggle_pause)
        self._drawer.preview_pressed.connect(self.toggle_preview)
        self._drawer.preview_seeked.connect(self._seek_fraction)
        self._drawer.stop_pressed.connect(self.stop_recording)
        self._drawer.open_pressed.connect(self.open_take)
        self._drawer.back_pressed.connect(self.close_take)
        self._drawer.play_pressed.connect(self.toggle_playback)
        self._drawer.seeked.connect(self._seek_fraction)
        self._drawer.rename_pressed.connect(self.rename_selected)
        self._drawer.delete_pressed.connect(self.delete_selected)
        self._drawer.folder_pressed.connect(self._open_recordings_folder)
        self._drawer.hide()
        shell_layout.addWidget(self._drawer)

        central.setStyleSheet("background-color: #3b4252; color: #c7d2e3;")
        shell.setStyleSheet("background-color: #3b4252; color: #c7d2e3;")
        self.setCentralWidget(shell)
        self.resize(TUNER_WIDTH, 190)
        self._place_handle()
        self.refresh_takes()
        self._restore_settings()

    def eventFilter(self, watched, event):
        """Keep the overlaid handle on the column's right edge as it resizes."""
        if event.type() == QEvent.Type.Resize and watched is self._tuner_column:
            self._place_handle()
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move) and watched is self._meter:
            self._place_beat_lane()
        return super().eventFilter(watched, event)

    def _place_beat_lane(self) -> None:
        meter = self._meter.geometry()
        self._beat_lane.setGeometry(
            meter.x() + 4, meter.y() + 2, self._beat_lane.width(), max(meter.height() - 4, 0)
        )

    def _place_handle(self) -> None:
        """The tab sits on the column's right edge at its vertical centre.

        Only moved, never resized: the handle is its own fixed size, so the
        strip of column above and below it stays the meter's, the level bar's
        and the trace's.
        """
        column = self._tuner_column
        handle = self._drawer_handle
        handle.move(column.width() - handle.width(), (column.height() - handle.height()) // 2)
        handle.raise_()

    def start(self) -> None:
        self._engine.start(self._device_combo.currentData())

    def closeEvent(self, event) -> None:
        if self.recording:  # a performance is never dropped on the way out
            self.stop_recording()
        self._put_take_away()
        if self._capture.recording:  # never drop what was being recorded
            self._session_timer.stop()
            try:
                print(f"리포트 저장됨: {self.toggle_report_session()}")
            except (RuntimeError, OSError) as error:
                print(f"리포트 저장 실패: {error}")
        self._save_settings()
        self._metronome_bar.stop()  # the output device outlives nothing
        self._engine.stop()
        super().closeEvent(event)

    def _restore_settings(self) -> None:
        s = self._settings
        # Restored before the geometry below, so that the saved size is the
        # one that matches the state the drawer comes back in.
        if s.value(DRAWER_OPEN_KEY, False, type=bool):
            self.set_drawer_open(True)

        stored_a4 = s.value("a4_hz", 442, type=int)
        self._a4_spin.setValue(stored_a4 if isinstance(stored_a4, int) else 442)
        # explicit sync: setValue emits no signal when the value is unchanged
        # (e.g. stored value == default), and the ctor's setValue predates connect
        self._engine.set_a4(float(self._a4_spin.value()))

        device_index = self._device_combo.findText(str(s.value("device_name", "")))
        if device_index >= 0:  # the stored device may be unplugged; ids aren't stable either
            with QSignalBlocker(self._device_combo):  # engine isn't started yet — no restart
                self._device_combo.setCurrentIndex(device_index)

        # same shape as the A4 restore above: a settings file edited by hand
        # or written by an older build must not take the window down with it
        stored_bpm = s.value("bpm", DEFAULT_BPM, type=float)
        self._metronome_bar.set_bpm(
            stored_bpm if isinstance(stored_bpm, float) else DEFAULT_BPM
        )
        self._metronome_bar.set_sound(str(s.value("metronome_sound", DEFAULT_SOUND)))
        stored_bar = s.value("metronome_beats_per_bar", DEFAULT_BEATS_PER_BAR, type=int)
        self._metronome.set_beats_per_bar(
            stored_bar if isinstance(stored_bar, int) else DEFAULT_BEATS_PER_BAR
        )
        stored_volume = s.value("metronome_volume", CLICK_AMPLITUDE, type=float)
        self._metronome_bar.set_volume(
            stored_volume if isinstance(stored_volume, float) else CLICK_AMPLITUDE
        )

        stored_gate = s.value("input_gate_dbfs", self._engine.input_gate_dbfs, type=float)
        if isinstance(stored_gate, float):
            self._engine.set_input_gate_dbfs(stored_gate)
            self._level_bar.set_gate(stored_gate)

        self._pin_check.setChecked(bool(s.value("always_on_top", False, type=bool)))

        geometry = s.value(GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        s = self._settings
        s.setValue("a4_hz", self._a4_spin.value())
        s.setValue("device_name", self._device_combo.currentText())
        s.setValue("bpm", self._metronome.bpm)
        s.setValue("metronome_volume", self._metronome.volume)
        s.setValue("metronome_sound", self._metronome.sound)
        s.setValue("metronome_beats_per_bar", self._metronome.beats_per_bar)
        s.setValue("input_gate_dbfs", self._engine.input_gate_dbfs)
        s.setValue("always_on_top", self._pin_check.isChecked())
        s.setValue(DRAWER_OPEN_KEY, self.drawer_open)
        s.setValue(GEOMETRY_KEY, self.saveGeometry())
        s.sync()

    # readings arrive every 5.8ms; ~2s of EXACT digital silence means the OS
    # is not delivering audio (permission / dead device), not a quiet room
    NO_SIGNAL_READINGS = 340

    def _on_reading(self, reading: TunerReading) -> None:
        self._meter.set_reading(reading)
        self._trace.add_reading(reading)
        self._level_bar.set_level(reading.level_dbfs)
        if self.recording and not self.paused:
            # The same level the bar shows, kept as a short trail: proof that
            # the take is still being recorded. Paused, nothing is being
            # recorded, so the trail stands still where it stopped - it used to
            # keep scrolling in a dimmer colour, which read as still recording.
            self._drawer.push_level(level_fraction(reading.level_dbfs))

        if reading.level_dbfs <= DIGITAL_SILENCE_DBFS:
            self._silent_readings += 1
            if self._silent_readings == self.NO_SIGNAL_READINGS:
                self._no_signal.show()
        else:
            self._silent_readings = 0
            if not self._no_signal.isHidden():
                self._no_signal.hide()

    def save_report(self) -> Path | None:
        """Freeze the last seconds of input and what the meter showed.

        Bound to Ctrl+R. The point is that it works *after* the fact: the
        ring buffer already holds the sound that misbehaved.
        """
        try:
            directory = self._capture.save(
                detector=self._engine.detector_name,
                a4_hz=float(self._a4_spin.value()),
                extra={"device": self._device_combo.currentText()},
            )
        except (RuntimeError, OSError) as error:
            self._flash(f"리포트 저장 실패: {error}", ok=False)
            return None
        self._flash(f"직전 {RING_SECONDS:g}초 저장됨 - {directory}")
        return directory

    def _shortcut(self, key: str, slot) -> QShortcut:
        shortcut = QShortcut(QKeySequence(key), self)
        shortcut.activated.connect(slot)
        return shortcut

    # --- the recorder ------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self._recorder.running

    @property
    def paused(self) -> bool:
        return self._recorder.paused

    @property
    def drawer_open(self) -> bool:
        return self._drawer.isVisible()

    def set_drawer_open(self, is_open: bool) -> None:
        """Open or close the drawer, resizing the window by exactly its width.

        The tuner column keeps its own geometry: only the window's right edge
        moves, so nothing the player was looking at shifts under them.
        """
        if is_open == self.drawer_open:
            return
        # Measured *before* the panel appears: showing it raises the window's
        # minimum width, and Qt grows the window to meet it on the spot. Adding
        # the drawer's width to the number read afterwards would therefore add
        # it twice (measured: 331 -> 731 instead of 531).
        # Both measured *before* the panel appears, for the same reason: the
        # moment it is shown Qt grows the window to the panel's minimum. Height
        # was once read afterwards and so remembered the drawer's 250 as the
        # tuner's own height, which closing then dutifully restored.
        was, height = self.width(), self.height()
        self._drawer.setVisible(is_open)
        self._drawer_handle.set_open(is_open)
        if not self.isMaximized() and not self.isFullScreen():
            if is_open and height < DRAWER_MIN_HEIGHT:
                self._height_before_drawer = height
                height = DRAWER_MIN_HEIGHT
            elif not is_open and self._height_before_drawer is not None:
                height = self._height_before_drawer
                self._height_before_drawer = None
            # Showing the drawer raises the window's *explicit* minimum size
            # (Qt writes the layout's minimum onto the window), and hiding it
            # again does not lower it — so a resize would be clamped and the
            # window would stay open-sized forever. Dropping the minimum lets
            # the resize through; the layout writes its own back on the next
            # pass, which is why reopening lands on exactly the same numbers.
            # Both axes, now that the drawer has a minimum height of its own.
            self.setMinimumSize(0, 0)
            delta = DRAWER_WIDTH if is_open else -DRAWER_WIDTH
            self.resize(was + delta, height)

    def toggle_drawer(self) -> None:
        self.set_drawer_open(not self.drawer_open)

    def toggle_recording(self) -> None:
        """Alt+R, or the drawer's mic. Starts a take and shows it."""
        if self.recording:
            self.stop_recording()
            return
        self._put_take_away()  # listening and recording are not done at once
        try:
            # the reference the take *began* at goes into its signature
            self._recorder.start(a4_hz=float(self._a4_spin.value()))
        except OSError as error:
            self._drawer.say(f"녹음을 시작할 수 없습니다: {error}", ok=False)
            self.set_drawer_open(True)
            return
        self._drawer.show_recording()
        self._drawer.say("")
        self._recorder_timer.start()
        self.set_drawer_open(True)

    def toggle_pause(self) -> None:
        """Alt+P. Does nothing at all when no take is running — a key that is
        not applicable says nothing rather than complaining."""
        if not self.recording:
            return
        if self.paused:
            # Resuming: whatever is being heard back stops *first*. Otherwise
            # the speakers would play the take into the microphone that is
            # recording it.
            self._put_take_away()
        paused = self._recorder.toggle_pause()
        self._drawer.set_paused(paused)
        if paused:  # the player row opens showing how much there is to hear
            self._drawer.set_preview_position(0.0, self._recorder.seconds)

    def toggle_preview(self) -> None:
        """Hear the paused take up to where it was paused, or stop hearing it."""
        if not (self.recording and self.paused):
            return
        if self._player.playing:
            self._put_take_away()
            return
        try:  # read afresh each time: a finished preview starts over
            signal = self._recorder.so_far()
        except RuntimeError as error:
            self._drawer.say(str(error), ok=False)
            return
        self._player.load_signal(signal, self._recorder.sample_rate)
        self._player.play()
        self._play_timer.start()
        self._drawer.set_previewing(True)
        self._drawer.set_preview_position(0.0, self._player.duration_seconds)

    def stop_recording(self) -> Path | None:
        """Alt+S, or the drawer's stop. Ends the take and asks what to call it."""
        if not self.recording:
            return None
        self._put_take_away()  # a take being heard back ends with the take
        self._recorder_timer.stop()
        self._drawer.show_list()
        try:
            seconds = self._recorder.stop()
        except RuntimeError as error:
            self._drawer.say(str(error), ok=False)
            return None
        return self._offer_to_save(seconds)

    def _offer_to_save(self, seconds: float) -> Path | None:
        """The one irreversible moment in the feature, so it asks twice: once
        for a name, and again before throwing anything away.

        The take is already on disk by now (it streamed while it was played),
        so keeping it is a rename and nothing here waits on an encoder.
        """
        if seconds < MIN_TAKE_SECONDS:
            # Too short to be a performance - almost certainly two presses in a
            # row. Saying so beats opening a dialog about nothing.
            self._recorder.discard()
            self._drawer.say(f"녹음이 너무 짧습니다 ({seconds:.1f}초)", ok=False)
            return None
        a4_hz = float(self._a4_spin.value())
        dialog = SaveTakeDialog(recorder.default_title(), seconds, a4_hz, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._recorder.discard()
            self._drawer.say("녹음을 버렸습니다")
            return None
        try:
            path = self._recorder.finish(dialog.title)
        except (RuntimeError, OSError) as error:
            self._drawer.say(f"녹음 저장 실패: {error}", ok=False)
            return None
        self.refresh_takes()
        self._drawer.say(f"저장됨 — {path.stem}")
        return path

    # --- the list of takes --------------------------------------------------

    def refresh_takes(self, select: int = 0) -> None:
        self._takes = recorder.list_takes(self._recordings_dir)
        rows = []
        for heading, takes in recorder.grouped(self._takes):
            for i, take in enumerate(takes):
                rows.append(
                    (heading if i == 0 else "", take.title, take.when_text, take.duration_text)
                )
        self._drawer.set_takes(rows, keep=select)

    def _selected_take(self):
        index = self._drawer.selected_index
        if 0 <= index < len(self._takes):
            return index, self._takes[index]
        return -1, None

    # --- listening ------------------------------------------------------------

    def open_take(self, index: int) -> None:
        """A row was clicked: load it and show the playback page.

        Loaded, not started. The Windows recorder opens a take paused at zero
        and so does this - the row that was clicked is the one you meant to
        look at, which is not always the one you meant to hear.
        """
        if not 0 <= index < len(self._takes):
            return
        take = self._takes[index]
        try:
            seconds = self._player.load_file(take.path)
        except (RuntimeError, OSError) as error:
            self._drawer.say(f"열 수 없습니다: {error}", ok=False)
            return
        self._drawer.show_playback(take.title, take.when_text, seconds)
        self._drawer.say("")
        self._play_timer.start()
        self._analyse_take(take.path)

    # --- the take's intonation ------------------------------------------------

    def _analyse_take(self, path: Path) -> None:
        """Draw what the meter would have shown for this take.

        From the cache if it was already done, otherwise on a worker thread:
        the analysis runs the real engine at about three times real time, far
        too slow for the GUI thread, and the graph fills in as it goes.
        """
        self._analysis_generation += 1
        generation = self._analysis_generation
        key = _cache_key(path)
        cached = self._pitch_cache.get(key) if key else None
        if cached is not None:
            self._drawer.set_pitch_track(cached)
            return

        def still_wanted() -> bool:
            return self._analysis_generation == generation

        def work() -> None:
            try:
                signal_, sr = sf.read(str(path), dtype="float64", always_2d=True)
                track = take_analysis.analyse(
                    signal_.mean(axis=1),
                    int(sr),
                    recorder.take_a4(path),
                    on_progress=lambda t: self._bridge.pitch_track.emit(generation, t),
                    should_stop=lambda: not still_wanted(),
                )
            except Exception as error:  # noqa: BLE001 - reported on the graph
                self._bridge.pitch_track.emit(generation, error)
                return
            if track is not None and key is not None:
                self._pitch_cache[key] = track

        threading.Thread(target=work, name="take-analysis", daemon=True).start()

    def _on_pitch_track(self, generation: int, track) -> None:
        if generation != self._analysis_generation:
            return  # a take that is no longer open
        if isinstance(track, Exception):
            self._drawer.set_pitch_message("음정을 분석하지 못했습니다")
            return
        self._drawer.set_pitch_track(track)

    def close_take(self) -> None:
        """The back arrow."""
        self._put_take_away()
        self._drawer.show_list()

    def _put_take_away(self) -> None:
        """Give the device back and stop the clock that was drawing it.

        The player and its tick are one thing. They used to be stopped side by
        side at each place a take was put away, and the place that starts a
        recording stopped only the player - leaving the tick redrawing a hidden
        page for the whole take.
        """
        self._play_timer.stop()
        self._player.stop()
        self._drawer.set_previewing(False)
        self._analysis_generation += 1  # whatever was being analysed, stop

    def toggle_playback(self) -> None:
        if not self._player.loaded:
            return
        self._player.toggle()
        self._drawer.set_playing(self._player.playing)

    def _seek_fraction(self, fraction: float) -> None:
        self._player.seek_seconds(fraction * self._player.duration_seconds)
        self._tick_playback()

    def _tick_playback(self) -> None:
        """Drives the clock and the seek bar, and notices the end.

        Rendering cannot report the end itself - touching the device from its
        own callback is how PortAudio deadlocks - so the view's own timer is
        what sees `playing` go false and puts the button back.
        """
        self._drawer.set_position(
            self._player.position_seconds, self._player.duration_seconds
        )
        self._drawer.set_playing(self._player.playing)
        self._drawer.set_previewing(self._player.playing)
        if self.recording:
            self._drawer.set_preview_position(
                self._player.position_seconds, self._player.duration_seconds
            )

    def rename_selected(self) -> None:
        index, take = self._selected_take()
        if take is None:
            return
        new_title, ok = QInputDialog.getText(self, "이름 바꾸기", "제목", text=take.title)
        if not ok or not new_title.strip():
            return
        playing_this = self._player.loaded and self._player.path == take.path
        if playing_this:
            self._put_take_away()  # the file is about to move out from under it
        try:
            renamed = recorder.rename_take(take.path, new_title.strip())
        except OSError as error:
            self._drawer.say(f"이름 바꾸기 실패: {error}", ok=False)
            return
        self.refresh_takes(select=index)
        self._drawer.say(f"이름 바꿈 — {renamed.stem}")
        if playing_this:
            self._drawer.show_list()

    def delete_selected(self) -> None:
        index, take = self._selected_take()
        if take is None:
            return
        answer = QMessageBox.question(
            self,
            "녹음 삭제",
            f"{take.title} 을(를) 지웁니다. 되돌릴 수 없습니다.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Discard:
            return
        self._put_take_away()
        try:
            take.path.unlink()
        except OSError as error:
            self._drawer.say(f"삭제 실패: {error}", ok=False)
            return
        self.refresh_takes(select=max(index - 1, 0))
        self._drawer.show_list()
        self._drawer.say(f"삭제됨 — {take.title}")

    def _open_recordings_folder(self) -> None:
        self._recordings_dir.mkdir(parents=True, exist_ok=True)
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._recordings_dir))):
            self._drawer.say(str(self._recordings_dir))
        else:
            self._drawer.say(f"폴더를 열지 못했습니다 — {self._recordings_dir}", ok=False)

    def _tick_recorder(self) -> None:
        self._drawer.set_elapsed(self._recorder.seconds)

    def toggle_report_session(self) -> Path | None:
        """The 리포트 button: start keeping everything, or stop and write it."""
        if not self._capture.recording:
            self._capture.start_recording()
            self._session_button.setText(SESSION_ACTIVE_TEXT.format("0:00"))
            self._session_timer.start()
            self._flash("리포트 기록 시작 — 다시 누르면 그 사이 전 구간이 저장된다")
            return None

        self._session_timer.stop()
        self._session_button.setText(SESSION_IDLE_TEXT)
        try:
            directory = self._capture.save_recording(
                detector=self._engine.detector_name,
                a4_hz=float(self._a4_spin.value()),
                extra={"device": self._device_combo.currentText()},
            )
        except (RuntimeError, OSError) as error:
            self._flash(f"리포트 저장 실패: {error}", ok=False)
            return None
        note = "장치가 바뀌어 중간에 끊겼다 — " if self._capture.interrupted else ""
        self._flash(f"{note}리포트 저장됨 - {directory}")
        return directory

    def _tick_session(self) -> None:
        seconds = self._capture.recorded_seconds
        self._session_button.setText(
            SESSION_ACTIVE_TEXT.format(f"{int(seconds) // 60}:{int(seconds) % 60:02d}")
        )
        if seconds >= MAX_SESSION_SECONDS:  # memory cap: stop before it hurts
            self.toggle_report_session()

    NOTE_MS = 6000  # long enough to read a path, short enough not to nag

    def _flash(self, text: str, ok: bool = True) -> None:
        colours = ("#3b5a3b", "#d6f5d6") if ok else ("#7a3b3b", "#ffd9d9")
        self._note.setStyleSheet(
            f"background-color: {colours[0]}; color: {colours[1]}; padding: 6px;"
        )
        self._note.setText(text)
        self._note.show()
        QTimer.singleShot(self.NOTE_MS, self._note.hide)

    def _populate_devices(self, devices) -> None:
        default_index = 0
        for i, device in enumerate(devices):
            self._device_combo.addItem(device.name, device.id)
            if device.is_default:
                default_index = i
        self._device_combo.setCurrentIndex(default_index)

    def _refresh_device_list(self) -> None:
        """Hot-plug support: rescan hardware when the dropdown opens."""
        previous = self._device_combo.currentText()
        self._engine.stop()  # the rescan invalidates any open stream
        self._audio_input.refresh_devices()
        devices = self._audio_input.list_devices()

        with QSignalBlocker(self._device_combo):
            self._device_combo.clear()
            self._populate_devices(devices)
            restored = self._device_combo.findText(previous)
            if restored >= 0:
                self._device_combo.setCurrentIndex(restored)
        # the previous device may be gone (now the default is selected) and
        # device ids may have shifted either way — restart on current data
        self._engine.start(self._device_combo.currentData())

    def _on_device_changed(self) -> None:
        self._engine.stop()
        self._engine.start(self._device_combo.currentData())

    def _on_pin_toggled(self, checked: bool) -> None:
        handle = self.windowHandle()
        if handle is not None:
            # flip the flag on the native window directly: QWidget.setWindowFlag
            # destroys and recreates the platform window, which flashes the
            # whole app off/on screen. The QWindow path just adjusts the OS
            # window level in place.
            flags = handle.flags()
            if checked:
                flags |= Qt.WindowType.WindowStaysOnTopHint
            else:
                flags &= ~Qt.WindowType.WindowStaysOnTopHint
            handle.setFlags(flags)
        else:
            # window not created yet (settings restore during __init__) —
            # setWindowFlag is fine here, and its hide side effect needs the
            # pre-call visibility to decide on a re-show
            was_visible = self.isVisible()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
            if was_visible:
                self.show()
