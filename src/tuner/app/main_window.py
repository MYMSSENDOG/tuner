"""Main window: meter + A4 reference, input device selection, always-on-top."""

from __future__ import annotations

import signal

from PySide6.QtCore import QObject, QSettings, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tuner.app.engine import DIGITAL_SILENCE_DBFS, TunerEngine, TunerReading
from tuner.app.level_widget import InputLevelBar
from tuner.app.meter_widget import MeterWidget
from tuner.app.trace_widget import PitchTraceWidget
from tuner.audio.input import AudioInput
from tuner.core.detector import DETECTORS


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
        self._engine = TunerEngine(audio_input, self._bridge.reading.emit)

        self._meter = MeterWidget()
        self._bridge.reading.connect(self._on_reading)

        controls = QHBoxLayout()
        controls.setContentsMargins(10, 8, 10, 4)

        controls.addWidget(QLabel("A4"))
        self._a4_spin = QSpinBox()
        self._a4_spin.setRange(415, 466)
        self._a4_spin.setValue(442)
        self._a4_spin.setSuffix(" Hz")
        self._a4_spin.lineEdit().setReadOnly(True)  # up/down buttons only, 1Hz steps
        self._a4_spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._a4_spin.valueChanged.connect(lambda v: self._engine.set_a4(float(v)))
        controls.addWidget(self._a4_spin)

        controls.addSpacing(12)
        controls.addWidget(QLabel("Input"))
        self._audio_input = audio_input
        self._device_combo = _DeviceComboBox()
        self._device_combo.setMinimumWidth(180)
        self._populate_devices(audio_input.list_devices())
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._device_combo.about_to_show.connect(self._refresh_device_list)
        controls.addWidget(self._device_combo, stretch=1)

        self._detector_combo = QComboBox()
        for detector_cls in DETECTORS:
            self._detector_combo.addItem(detector_cls.name, detector_cls)
        self._detector_combo.currentIndexChanged.connect(self._on_detector_changed)
        controls.addWidget(self._detector_combo)

        self._pin_check = QCheckBox("Always on top")
        self._pin_check.toggled.connect(self._on_pin_toggled)
        controls.addWidget(self._pin_check)

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
        layout.addWidget(self._meter, stretch=1)
        self._level_bar = InputLevelBar()
        layout.addWidget(self._level_bar)
        self._trace = PitchTraceWidget()
        layout.addWidget(self._trace)
        self._silent_readings = 0
        central.setStyleSheet("background-color: #3b4252; color: #c7d2e3;")
        self.setCentralWidget(central)
        self.resize(520, 650)
        self._restore_settings()

    def start(self) -> None:
        self._engine.start(self._device_combo.currentData())

    def closeEvent(self, event) -> None:
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

        geometry = s.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        s = self._settings
        s.setValue("a4_hz", self._a4_spin.value())
        s.setValue("device_name", self._device_combo.currentText())
        s.setValue("detector_name", self._detector_combo.currentText())
        s.setValue("always_on_top", self._pin_check.isChecked())
        s.setValue("geometry", self.saveGeometry())
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
