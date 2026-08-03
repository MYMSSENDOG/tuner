"""UI behavior offscreen: window wiring, readings, controls, device switching."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from tuner.audio.input import InputDevice  # noqa: E402
from tuner.core.tracker import State  # noqa: E402

from tests.fakes import FakeAudioInput  # noqa: E402
from tests.synth import tone  # noqa: E402


def make_window(fake=None):
    from tuner.app.main_window import MainWindow

    return MainWindow(fake if fake is not None else FakeAudioInput(tone(440.0, 0.05)))


def test_window_end_to_end(qapp):
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


def test_always_on_top_flag(qapp):
    window = make_window()
    window._pin_check.setChecked(True)
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window._pin_check.setChecked(False)
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.close()


def test_a4_spinbox_updates_engine(qapp):
    window = make_window()
    window._a4_spin.setValue(442)
    assert window._engine.a4_hz == 442.0
    window.close()


class TestDeviceSelection:
    DEVICES = (
        InputDevice(id=3, name="Mic A", is_default=False),
        InputDevice(id=7, name="Mic B", is_default=True),
    )

    def test_default_device_preselected_and_used(self, qapp):
        fake = FakeAudioInput(devices=self.DEVICES)
        window = make_window(fake)
        assert window._device_combo.currentText() == "Mic B"
        window.start()
        assert fake.started_with == [7]
        window.close()

    def test_switching_device_restarts_stream(self, qapp):
        fake = FakeAudioInput(devices=self.DEVICES)
        window = make_window(fake)
        window.start()
        stops_before = fake.stop_count

        window._device_combo.setCurrentIndex(window._device_combo.findText("Mic A"))
        assert fake.stop_count > stops_before
        assert fake.started_with == [7, 3]
        window.close()

    def test_switch_resets_pipeline_state(self, qapp):
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
