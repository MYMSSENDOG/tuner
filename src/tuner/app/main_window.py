"""Main window: meter + A4 reference, input device selection, always-on-top."""

from __future__ import annotations

from PySide6.QtCore import QObject, QSettings, QSignalBlocker, Qt, Signal
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

from tuner.app.engine import TunerEngine, TunerReading
from tuner.app.meter_widget import MeterWidget
from tuner.audio.input import AudioInput
from tuner.core.detector import DETECTORS


class _ReadingBridge(QObject):
    """Marshals engine readings from the audio thread onto the GUI thread."""

    reading = Signal(object)


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
        self._a4_spin.setValue(440)
        self._a4_spin.setSuffix(" Hz")
        self._a4_spin.valueChanged.connect(lambda v: self._engine.set_a4(float(v)))
        controls.addWidget(self._a4_spin)

        controls.addSpacing(12)
        controls.addWidget(QLabel("Input"))
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(180)
        default_index = 0
        for i, device in enumerate(audio_input.list_devices()):
            self._device_combo.addItem(device.name, device.id)
            if device.is_default:
                default_index = i
        self._device_combo.setCurrentIndex(default_index)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
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
        layout.addWidget(self._meter, stretch=1)
        central.setStyleSheet("background-color: #3b4252; color: #c7d2e3;")
        self.setCentralWidget(central)
        self.resize(520, 560)
        self._restore_settings()

    def start(self) -> None:
        self._engine.start(self._device_combo.currentData())

    def closeEvent(self, event) -> None:
        self._save_settings()
        self._engine.stop()
        super().closeEvent(event)

    def _restore_settings(self) -> None:
        s = self._settings
        self._a4_spin.setValue(int(s.value("a4_hz", 440)))  # signal applies it to the engine

        device_index = self._device_combo.findText(str(s.value("device_name", "")))
        if device_index >= 0:  # the stored device may be unplugged; ids aren't stable either
            with QSignalBlocker(self._device_combo):  # engine isn't started yet — no restart
                self._device_combo.setCurrentIndex(device_index)

        detector_index = self._detector_combo.findText(str(s.value("detector_name", "")))
        if detector_index >= 0:
            with QSignalBlocker(self._detector_combo):
                self._detector_combo.setCurrentIndex(detector_index)
            self._engine.set_detector(self._detector_combo.currentData()())

        self._pin_check.setChecked(s.value("always_on_top", False, type=bool))

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

    def _on_reading(self, reading: TunerReading) -> None:
        self._meter.set_reading(reading)

    def _on_detector_changed(self) -> None:
        # the audio thread must not be mid-callback while the buffer is swapped
        self._engine.stop()
        self._engine.set_detector(self._detector_combo.currentData()())
        self._engine.start(self._device_combo.currentData())

    def _on_device_changed(self) -> None:
        self._engine.stop()
        self._engine.start(self._device_combo.currentData())

    def _on_pin_toggled(self, checked: bool) -> None:
        # setWindowFlag() hides the window as a side effect, so visibility
        # must be captured BEFORE the call — checking after skips the re-show
        # and the app exits with its last window closed
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        if was_visible:
            self.show()
