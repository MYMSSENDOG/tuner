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
from tuner.app.engine import DIGITAL_SILENCE_DBFS, TunerEngine, TunerReading
from tuner.app.level_widget import InputLevelBar
from tuner.app.meter_widget import MeterWidget
from tuner.app.trace_widget import PitchTraceWidget
from tuner.audio.input import AudioInput
from tuner.core.detector import DETECTORS

# Bumped when the default window size changes: a stored geometry from the
# tall layout would otherwise survive the update and re-stretch the window,
# so the old key is simply left behind and the new default applies once.
GEOMETRY_KEY = "geometry_v5"

# One key, no menu: the moment worth reporting has already passed when the
# player reaches for the mouse. Undiscoverable on purpose - the UI stays a
# meter (README documents it), and nothing about it costs anything until used.
REPORT_SHORTCUT = "Ctrl+R"
# Ctrl+R is for the moment already gone. This is the other half: sit down,
# press record, play the thing that misbehaves, press it again. Nothing is
# dropped in between, so the whole episode is on disk in one file.
RECORD_SHORTCUT = "Ctrl+L"
# === UI switch: set True to put the session-record button back on the
# === controls row. Off by default - the row is the window's width floor,
# === and the capture layer underneath it stays live either way (Ctrl+R,
# === FieldCapture, tools/promote). Flip this for a logging session.
RECORD_BUTTON_ENABLED = False


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


RECORD_IDLE_TEXT = "● 기록"
RECORD_ACTIVE_TEXT = "■ 기록 중 {}"


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
    def __init__(self, audio_input: AudioInput, settings: QSettings | None = None):
        super().__init__()
        self.setWindowTitle("Tuner")
        self._settings = settings if settings is not None else QSettings("tuner", "tuner")

        self._bridge = _ReadingBridge()
        self._capture = FieldCapture()
        self._engine = TunerEngine(
            audio_input, self._bridge.reading.emit, capture=self._capture
        )
        self._report_shortcut = QShortcut(QKeySequence(REPORT_SHORTCUT), self)
        self._report_shortcut.activated.connect(self.save_report)

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

        self._detector_combo = QComboBox()
        self._detector_combo.setToolTip("Detection algorithm")
        self._detector_combo.setMinimumContentsLength(3)
        self._detector_combo.setMinimumWidth(56)
        self._detector_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        for detector_cls in DETECTORS:
            self._detector_combo.addItem(detector_cls.name, detector_cls)
        self._detector_combo.currentIndexChanged.connect(self._on_detector_changed)
        controls.addWidget(self._detector_combo)

        self._pin_check = QCheckBox("Pin")
        self._pin_check.setToolTip("Always on top")
        self._pin_check.toggled.connect(self._on_pin_toggled)
        controls.addWidget(self._pin_check)

        self._record_button = QPushButton(RECORD_IDLE_TEXT)
        self._record_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._record_button.clicked.connect(self.toggle_recording)
        self._record_timer = QTimer(self)
        self._record_timer.setInterval(500)
        self._record_timer.timeout.connect(self._tick_recording)
        if RECORD_BUTTON_ENABLED:
            controls.addWidget(self._record_button)
            self._record_shortcut = QShortcut(QKeySequence(RECORD_SHORTCUT), self)
            self._record_shortcut.activated.connect(self.toggle_recording)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(controls)
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
        layout.addWidget(self._level_bar)
        self._trace = PitchTraceWidget()
        layout.addWidget(self._trace)
        self._silent_readings = 0
        central.setStyleSheet("background-color: #3b4252; color: #c7d2e3;")
        self.setCentralWidget(central)
        self.resize(310, 190)
        self._restore_settings()

    def start(self) -> None:
        self._engine.start(self._device_combo.currentData())

    def closeEvent(self, event) -> None:
        if self._capture.recording:  # never drop what was being recorded
            self._record_timer.stop()
            try:
                print(f"기록 저장됨: {self.toggle_recording()}")
            except (RuntimeError, OSError) as error:
                print(f"기록 저장 실패: {error}")
        self._save_settings()
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

        detector_index = self._detector_combo.findText(str(s.value("detector_name", "")))
        if detector_index >= 0:
            with QSignalBlocker(self._detector_combo):
                self._detector_combo.setCurrentIndex(detector_index)
            self._engine.set_detector(self._detector_combo.currentData()())

        self._pin_check.setChecked(bool(s.value("always_on_top", False, type=bool)))

        geometry = s.value(GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        s = self._settings
        s.setValue("a4_hz", self._a4_spin.value())
        s.setValue("device_name", self._device_combo.currentText())
        s.setValue("detector_name", self._detector_combo.currentText())
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
                detector=self._detector_combo.currentText(),
                a4_hz=float(self._a4_spin.value()),
                extra={"device": self._device_combo.currentText()},
            )
        except (RuntimeError, OSError) as error:
            self._flash(f"리포트 저장 실패: {error}", ok=False)
            return None
        self._flash(f"직전 {RING_SECONDS:g}초 저장됨 - {directory}")
        return directory

    def toggle_recording(self) -> Path | None:
        """The 기록 button: start keeping everything, or stop and write it."""
        if not self._capture.recording:
            self._capture.start_recording()
            self._record_button.setText(RECORD_ACTIVE_TEXT.format("0:00"))
            self._record_timer.start()
            self._flash("기록 시작 — 다시 누르면 그 사이 전 구간이 저장된다")
            return None

        self._record_timer.stop()
        self._record_button.setText(RECORD_IDLE_TEXT)
        try:
            directory = self._capture.save_recording(
                detector=self._detector_combo.currentText(),
                a4_hz=float(self._a4_spin.value()),
                extra={"device": self._device_combo.currentText()},
            )
        except (RuntimeError, OSError) as error:
            self._flash(f"기록 저장 실패: {error}", ok=False)
            return None
        note = "장치가 바뀌어 중간에 끊겼다 — " if self._capture.interrupted else ""
        self._flash(f"{note}기록 저장됨 - {directory}")
        return directory

    def _tick_recording(self) -> None:
        seconds = self._capture.recorded_seconds
        self._record_button.setText(
            RECORD_ACTIVE_TEXT.format(f"{int(seconds) // 60}:{int(seconds) % 60:02d}")
        )
        if seconds >= MAX_SESSION_SECONDS:  # memory cap: stop before it hurts
            self.toggle_recording()

    NOTE_MS = 6000  # long enough to read a path, short enough not to nag

    def _flash(self, text: str, ok: bool = True) -> None:
        colours = ("#3b5a3b", "#d6f5d6") if ok else ("#7a3b3b", "#ffd9d9")
        self._note.setStyleSheet(
            f"background-color: {colours[0]}; color: {colours[1]}; padding: 6px;"
        )
        self._note.setText(text)
        self._note.show()
        QTimer.singleShot(self.NOTE_MS, self._note.hide)

    def _on_detector_changed(self) -> None:
        # the audio thread must not be mid-callback while the buffer is swapped
        self._engine.stop()
        self._engine.set_detector(self._detector_combo.currentData()())
        self._engine.start(self._device_combo.currentData())

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
