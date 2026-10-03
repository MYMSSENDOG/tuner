"""Main window: meter + A4 reference, input device selection, always-on-top."""

from __future__ import annotations

import signal
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tuner.app.capture import MAX_SESSION_SECONDS, RING_SECONDS, FieldCapture
from tuner.app.drawer_handle import WIDTH as HANDLE_WIDTH
from tuner.app.drawer_handle import DrawerHandle
from tuner.app.engine import DIGITAL_SILENCE_DBFS, TunerEngine, TunerReading
from tuner.app.level_widget import InputLevelBar
from tuner.app.level_widget import fill_fraction as level_fraction
from tuner.app.meter_widget import MeterWidget
from tuner.app.metronome import MetronomeService
from tuner.app.metronome_widget import MetronomeBar
from tuner.app.recordings_drawer import WIDTH as DRAWER_WIDTH
from tuner.app.recordings_drawer import RecordingsDrawer
from tuner.app.trace_widget import PitchTraceWidget
from tuner.audio.input import AudioInput
from tuner.audio.output import AudioOutput
from tuner.audio.sounddevice_output import SoundDeviceOutput
from tuner.core.metronome import CLICK_AMPLITUDE, DEFAULT_BPM, DEFAULT_SOUND

# Bumped when the default window size changes: a stored geometry from the
# tall layout would otherwise survive the update and re-stretch the window,
# so the old key is simply left behind and the new default applies once.
# v6: the detector combo left the controls row, which was the window's width
# floor - 354px measured, above the 310 this asks for, so the window had been
# silently wider than its own default. Without it the floor is 294 and the
# default is reachable, but only for someone who is not carrying a v5
# geometry.
# v7: the drawer handle was welded to the right edge, so the default grew by
# its 11px. A stored v6 geometry would have left the tuner column 11px
# narrower than it has always been.
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

RECORD_IDLE_TEXT = "●"
RECORD_ACTIVE_TEXT = "●"
RECORD_BUTTON_W = 22  # the dot and its padding; the row has 16px of slack
RECORD_IDLE_STYLE = (
    "background-color: #4a2b2e; border: 1px solid #7d3f43; color: #ff8a8a;"
    " padding: 2px 0;"
)
RECORD_ACTIVE_STYLE = (
    "background-color: #7a3b3b; border: 1px solid #a35151; color: #ffd9d9;"
    " padding: 2px 0;"
)


class _ReadingBridge(QObject):
    """Marshals engine readings from the audio thread onto the GUI thread."""

    reading = Signal(object)


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
        self._engine = TunerEngine(
            audio_input,
            self._bridge.reading.emit,
            capture=self._capture,
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
        self._recording = False
        self._paused = False

        self._meter = MeterWidget()
        self._bridge.reading.connect(self._on_reading)

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

        # The recorder's one always-visible control. A dot, not a word: the row
        # is the window's width floor and this is the only thing added to it.
        self._record_button = QPushButton(RECORD_IDLE_TEXT)
        self._record_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._record_button.setToolTip(f"녹음 ({RECORD_SHORTCUT})")
        self._record_button.setFixedWidth(RECORD_BUTTON_W)
        self._record_button.setStyleSheet(RECORD_IDLE_STYLE)
        self._record_button.clicked.connect(self.toggle_recording)
        controls.addWidget(self._record_button)

        self._session_button = QPushButton(SESSION_IDLE_TEXT)
        self._session_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._session_button.clicked.connect(self.toggle_report_session)
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(500)
        self._session_timer.timeout.connect(self._tick_session)
        if REPORT_SESSION_BUTTON_ENABLED:
            controls.addWidget(self._session_button)
            self._session_shortcut = QShortcut(QKeySequence(REPORT_SESSION_SHORTCUT), self)
            self._session_shortcut.activated.connect(self.toggle_report_session)

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
        self._drawer_handle = DrawerHandle()
        self._drawer_handle.toggled.connect(self.toggle_drawer)
        shell_layout.addWidget(self._drawer_handle)
        self._drawer = RecordingsDrawer()
        self._drawer.pause_pressed.connect(self.toggle_pause)
        self._drawer.stop_pressed.connect(self.stop_recording)
        self._drawer.folder_pressed.connect(self._open_recordings_folder)
        self._drawer.hide()
        shell_layout.addWidget(self._drawer)

        central.setStyleSheet("background-color: #3b4252; color: #c7d2e3;")
        shell.setStyleSheet("background-color: #3b4252; color: #c7d2e3;")
        self.setCentralWidget(shell)
        self.resize(TUNER_WIDTH + HANDLE_WIDTH, 190)
        self._restore_settings()

    def start(self) -> None:
        self._engine.start(self._device_combo.currentData())

    def closeEvent(self, event) -> None:
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
        s.setValue("input_gate_dbfs", self._engine.input_gate_dbfs)
        s.setValue("always_on_top", self._pin_check.isChecked())
        s.setValue(GEOMETRY_KEY, self.saveGeometry())
        s.sync()

    # readings arrive every 5.8ms; ~2s of EXACT digital silence means the OS
    # is not delivering audio (permission / dead device), not a quiet room
    NO_SIGNAL_READINGS = 340

    def _on_reading(self, reading: TunerReading) -> None:
        self._meter.set_reading(reading)
        self._trace.add_reading(reading)
        self._level_bar.set_level(reading.level_dbfs)
        if self._recording:
            # The same level the bar shows, kept as a short trail: proof that
            # the microphone is still live without drawing the file's waveform.
            self._drawer.push_level(level_fraction(reading.level_dbfs), live=not self._paused)

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

    # --- the recorder (UI only so far: no audio is written yet) ------------

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def paused(self) -> bool:
        return self._paused

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
        was = self.width()
        self._drawer.setVisible(is_open)
        self._drawer_handle.set_open(is_open)
        if not self.isMaximized() and not self.isFullScreen():
            # Showing the drawer raises the window's *explicit* minimum width
            # (Qt writes the layout's minimum onto the window), and hiding it
            # again does not lower it — so a resize would be clamped and the
            # window would stay open-width forever. Dropping the minimum lets
            # the resize through; the layout writes its own back on the next
            # pass, which is why reopening lands on exactly the same number.
            self.setMinimumWidth(0)
            delta = DRAWER_WIDTH if is_open else -DRAWER_WIDTH
            self.resize(was + delta, self.height())

    def toggle_drawer(self) -> None:
        self.set_drawer_open(not self.drawer_open)

    def toggle_recording(self) -> None:
        """Alt+R / the dot. Starts a take and opens the drawer with it."""
        if self._recording:
            self.stop_recording()
            return
        self._recording = True
        self._paused = False
        self._record_button.setText(RECORD_ACTIVE_TEXT)
        self._record_button.setStyleSheet(RECORD_ACTIVE_STYLE)
        self._drawer.show_recording(True)
        self._drawer.set_paused(False)
        self.set_drawer_open(True)

    def toggle_pause(self) -> None:
        """Alt+P. Does nothing at all when no take is running — a key that is
        not applicable says nothing rather than complaining."""
        if not self._recording:
            return
        self._paused = not self._paused
        self._drawer.set_paused(self._paused)

    def stop_recording(self) -> None:
        """Alt+S. Ends the take; the save dialog belongs to the next step."""
        if not self._recording:
            return
        self._recording = False
        self._paused = False
        self._record_button.setText(RECORD_IDLE_TEXT)
        self._record_button.setStyleSheet(RECORD_IDLE_STYLE)
        self._drawer.show_recording(False)
        self._flash("녹음 중지 — 저장은 아직 붙지 않았습니다")

    def _open_recordings_folder(self) -> None:
        self._flash("녹음 폴더는 저장 기능과 함께 붙습니다")

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
