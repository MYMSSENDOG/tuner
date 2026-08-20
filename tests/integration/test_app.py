"""UI behavior offscreen: window wiring, readings, controls, device switching."""

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from tests.fakes import FakeAudioInput
from tests.synth import tone
from tuner.audio.input import InputDevice
from tuner.core.tracker import State


@pytest.fixture
def make_window(qapp, tmp_path):
    """Window factory with per-test isolated settings — tests must never
    touch (or be influenced by) the real user QSettings."""
    from PySide6.QtCore import QSettings

    from tuner.app.main_window import MainWindow

    def factory(fake=None):
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        return MainWindow(fake if fake is not None else FakeAudioInput(tone(440.0, 0.05)), settings)

    return factory


def test_window_end_to_end(qapp, make_window):
    fake = FakeAudioInput(tone(440.0, 0.3, instrument="violin"))
    window = make_window(fake)
    window.show()
    window.start()
    fake.pump()
    qapp.processEvents()  # deliver queued cross-thread signals

    reading = window._meter._reading
    assert reading is not None
    assert reading.state is State.OK
    assert reading.note.label == "A4"
    window.close()


def test_always_on_top_flag(make_window):
    window = make_window()
    window._pin_check.setChecked(True)
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window._pin_check.setChecked(False)
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.close()


def test_pin_toggle_on_visible_window(make_window):
    """On a shown window the pin flips the flag on the native window handle
    (no widget-level flag change: that recreates the platform window, which
    once flashed the app off screen / quit it entirely)."""
    window = make_window()
    window.show()
    window._pin_check.setChecked(True)
    assert window.isVisible()
    assert window.windowHandle().flags() & Qt.WindowType.WindowStaysOnTopHint
    window._pin_check.setChecked(False)
    assert window.isVisible()
    assert not window.windowHandle().flags() & Qt.WindowType.WindowStaysOnTopHint
    window.close()


def test_a4_defaults_and_button_only(make_window):
    window = make_window()
    assert window._a4_spin.value() == 442  # product default
    assert window._engine.a4_hz == 442.0
    assert window._a4_spin.lineEdit().isReadOnly()  # arrows only, no typing
    window._a4_spin.stepUp()
    assert window._a4_spin.value() == 443 and window._engine.a4_hz == 443.0
    window._a4_spin.stepDown()
    assert window._engine.a4_hz == 442.0
    window.close()


def test_meter_holds_last_value_after_silence(qapp):
    """After sound stops the meter keeps showing the last pitch as a ghost
    for a few seconds instead of blanking instantly."""
    from tuner.app.engine import TunerReading
    from tuner.app.meter_widget import HOLD_DISPLAY_S, MeterWidget
    from tuner.core.notes import freq_to_note

    meter = MeterWidget()
    ok = TunerReading(state=State.OK, note=freq_to_note(440.0))
    meter.set_reading(ok)
    meter.set_reading(TunerReading(state=State.SILENT, note=None))

    display, ghost = meter._display_reading()
    assert display is ok and ghost

    meter._last_ok_at -= HOLD_DISPLAY_S + 1  # simulate the hold expiring
    display, ghost = meter._display_reading()
    assert display is None and not ghost


def test_trace_freezes_on_silence_and_tracks_note_labels(qapp):
    from tuner.app.engine import TunerReading
    from tuner.app.trace_widget import PitchTraceWidget
    from tuner.core.notes import freq_to_note

    trace = PitchTraceWidget()
    trace.add_reading(TunerReading(state=State.OK, note=freq_to_note(441.0)))
    trace.add_reading(TunerReading(state=State.OK, note=freq_to_note(494.0)))  # A4 -> B4
    assert [label for _, label in trace._points] == ["A4", "B4"]

    # silence contributes exactly one gap, then the trace freezes
    for _ in range(10):
        trace.add_reading(TunerReading(state=State.SILENT, note=None))
    assert len(trace._points) == 3
    assert trace._points[-1] == (None, None)

    trace.add_reading(TunerReading(state=State.OK, note=freq_to_note(494.5)))
    assert len(trace._points) == 4  # resumes when sound returns


class TestInputLevelIndication:
    def test_reading_carries_level(self):
        import numpy as np

        from tests.integration.test_engine import run_engine

        amp = 10 ** (-20 / 20) * np.sqrt(2)  # sine with -20dBFS rms
        readings = run_engine(tone(440.0, 0.2) * amp)
        levels = [r.level_dbfs for r in readings]
        assert abs(np.median(levels) - (-20.0)) < 1.5

    def test_digital_silence_shows_banner_and_recovers(self, qapp, make_window):
        import numpy as np

        fake = FakeAudioInput(np.zeros(0))
        window = make_window(fake)
        window.start()
        assert window._no_signal.isHidden()

        fake._signal = np.zeros(120_000)  # ~2.7s of exact zeros
        fake.pump()
        qapp.processEvents()
        assert not window._no_signal.isHidden()

        fake._signal = tone(440.0, 0.3)
        fake.pump()
        qapp.processEvents()
        assert window._no_signal.isHidden()
        window.close()

    def test_level_bar_fill_mapping(self):
        from tuner.app.level_widget import GATE_DBFS, fill_fraction

        assert fill_fraction(-60.0) == 0.0
        assert fill_fraction(0.0) == 1.0
        assert fill_fraction(-200.0) == 0.0  # clamped
        assert abs(GATE_DBFS - (-40.0)) < 0.01  # derived from the gate constant
        assert 0.0 < fill_fraction(GATE_DBFS) < 1.0


class TestSettingsPersistence:
    DEVICES = (
        InputDevice(id=3, name="Mic A", is_default=False),
        InputDevice(id=7, name="Mic B", is_default=True),
    )

    def make_settings(self, tmp_path):
        from PySide6.QtCore import QSettings

        return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)

    def test_settings_roundtrip(self, qapp, tmp_path):
        from tuner.app.main_window import MainWindow

        window = MainWindow(FakeAudioInput(devices=self.DEVICES), self.make_settings(tmp_path))
        window._a4_spin.setValue(441)
        window._device_combo.setCurrentIndex(window._device_combo.findText("Mic A"))
        window._detector_combo.setCurrentIndex(1)
        window._pin_check.setChecked(True)
        window.close()

        restored = MainWindow(FakeAudioInput(devices=self.DEVICES), self.make_settings(tmp_path))
        assert restored._a4_spin.value() == 441
        assert restored._engine.a4_hz == 441.0
        assert restored._device_combo.currentText() == "Mic A"
        assert restored._detector_combo.currentIndex() == 1
        assert restored._pin_check.isChecked()
        assert restored.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        restored.close()

    def test_restore_does_not_start_stream(self, qapp, tmp_path):
        """Restoring device/detector must not open the audio stream early."""
        from tuner.app.main_window import MainWindow

        first = MainWindow(FakeAudioInput(devices=self.DEVICES), self.make_settings(tmp_path))
        first._device_combo.setCurrentIndex(0)
        first._detector_combo.setCurrentIndex(1)
        first.close()

        fake = FakeAudioInput(devices=self.DEVICES)
        restored = MainWindow(fake, self.make_settings(tmp_path))
        assert fake.started_with == []
        restored.start()
        assert fake.started_with == [3]  # the restored device, started once
        restored.close()

    def test_missing_device_falls_back_to_default(self, qapp, tmp_path):
        from tuner.app.main_window import MainWindow

        settings = self.make_settings(tmp_path)
        settings.setValue("device_name", "Unplugged Mic")
        settings.sync()
        window = MainWindow(FakeAudioInput(devices=self.DEVICES), self.make_settings(tmp_path))
        assert window._device_combo.currentText() == "Mic B"  # system default
        window.close()


class TestDeviceSelection:
    DEVICES = (
        InputDevice(id=3, name="Mic A", is_default=False),
        InputDevice(id=7, name="Mic B", is_default=True),
    )

    def test_default_device_preselected_and_used(self, make_window):
        fake = FakeAudioInput(devices=self.DEVICES)
        window = make_window(fake)
        assert window._device_combo.currentText() == "Mic B"
        window.start()
        assert fake.started_with == [7]
        window.close()

    def test_switching_device_restarts_stream(self, make_window):
        fake = FakeAudioInput(devices=self.DEVICES)
        window = make_window(fake)
        window.start()
        stops_before = fake.stop_count

        window._device_combo.setCurrentIndex(window._device_combo.findText("Mic A"))
        assert fake.stop_count > stops_before
        assert fake.started_with == [7, 3]
        window.close()

    def test_hotplug_refresh_adds_new_device(self, make_window):
        """Opening the dropdown rescans hardware: a device plugged in after
        launch appears, and the current selection survives the rebuild."""
        fake = FakeAudioInput(devices=self.DEVICES)
        window = make_window(fake)
        window.start()
        assert window._device_combo.count() == 2

        fake._devices.append(InputDevice(id=9, name="USB Mic", is_default=False))
        window._refresh_device_list()
        assert window._device_combo.count() == 3
        assert window._device_combo.currentText() == "Mic B"  # selection kept
        assert fake.started_with[-1] == 7  # stream restarted on the same device
        window.close()

    def test_hotplug_refresh_handles_removed_active_device(self, make_window):
        fake = FakeAudioInput(devices=self.DEVICES)
        window = make_window(fake)
        window.start()

        fake._devices = [d for d in fake._devices if d.name != "Mic B"]
        window._refresh_device_list()
        assert window._device_combo.currentText() == "Mic A"  # fell back
        assert fake.started_with[-1] == 3  # restarted on the surviving device
        window.close()

    def test_switch_resets_pipeline_state(self, qapp, make_window):
        """Buffered audio from the old device must not leak into the new stream."""
        fake = FakeAudioInput(tone(440.0, 0.2), devices=self.DEVICES)
        window = make_window(fake)
        window.start()
        fake.pump()
        qapp.processEvents()
        assert window._meter._reading.state is State.OK

        window._device_combo.setCurrentIndex(window._device_combo.findText("Mic A"))
        engine = window._engine
        assert engine._filled == 0 and engine._pending == 0
        window.close()


def test_report_shortcut_writes_a_replayable_report(qapp, make_window, tmp_path, monkeypatch):
    """Ctrl+R must save what the meter just showed, wired end to end: ring
    buffer -> report directory -> a trace the promote tool can replay."""
    from tuner.tools.promote import reproduce

    monkeypatch.setenv("TUNER_REPORTS_DIR", str(tmp_path / "reports"))
    fake = FakeAudioInput(tone(440.0, 0.5, instrument="violin"))
    window = make_window(fake)
    window.start()
    fake.pump()
    qapp.processEvents()

    directory = window.save_report()
    assert directory is not None and directory.exists()
    assert not window._note.isHidden()  # the user is told where it went

    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["a4_hz"] == float(window._a4_spin.value())
    assert meta["detector"] == window._detector_combo.currentText()

    captured, _, spans = reproduce(directory)
    assert captured.frames and spans == []
    window.close()


def test_report_says_so_when_there_is_nothing_to_save(make_window, tmp_path, monkeypatch):
    monkeypatch.setenv("TUNER_REPORTS_DIR", str(tmp_path / "reports"))
    window = make_window()  # never started: the ring is empty
    assert window.save_report() is None
    assert "실패" in window._note.text()
    window.close()


def test_record_button_keeps_the_whole_session(qapp, make_window, tmp_path, monkeypatch):
    """Press, play, press: everything in between lands in one report — the
    ring's 10 seconds are not the limit when you meant to record."""
    from tuner.app import main_window
    from tuner.app.main_window import RECORD_IDLE_TEXT

    monkeypatch.setattr(main_window, "RECORD_BUTTON_ENABLED", True)
    monkeypatch.setenv("TUNER_REPORTS_DIR", str(tmp_path / "reports"))
    fake = FakeAudioInput(tone(440.0, 0.5, instrument="violin"))
    window = make_window(fake)
    window.start()

    window._record_button.click()
    assert window._capture.recording
    assert "기록 중" in window._record_button.text()

    fake.pump()
    qapp.processEvents()
    directory = window.toggle_recording()  # the second press

    assert directory is not None
    assert window._record_button.text() == RECORD_IDLE_TEXT
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "session"
    assert meta["seconds"] == pytest.approx(0.5, abs=0.02)
    assert meta["frames"] > 50
    window.close()


def test_closing_mid_recording_saves_rather_than_drops(qapp, make_window, tmp_path, monkeypatch):
    from tuner.app import main_window

    monkeypatch.setattr(main_window, "RECORD_BUTTON_ENABLED", True)
    reports = tmp_path / "reports"
    monkeypatch.setenv("TUNER_REPORTS_DIR", str(reports))
    fake = FakeAudioInput(tone(440.0, 0.3, instrument="violin"))
    window = make_window(fake)
    window.start()
    window.toggle_recording()
    fake.pump()
    qapp.processEvents()

    window.close()
    assert not window._capture.recording
    saved = list(reports.glob("*/meta.json"))
    assert len(saved) == 1, "a recording in progress must not vanish on close"
