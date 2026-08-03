"""UI smoke test: window builds offscreen and readings reach the meter."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tuner.app.main_window import MainWindow  # noqa: E402
from tuner.core.tracker import State  # noqa: E402

from tests.synth import tone  # noqa: E402
from tests.test_engine import FakeAudioInput  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_window_end_to_end(qapp):
    fake = FakeAudioInput(tone(440.0, 0.3, instrument="violin"))
    window = MainWindow(fake)
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
    window = MainWindow(FakeAudioInput(tone(440.0, 0.05)))
    window._pin_check.setChecked(True)
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window._pin_check.setChecked(False)
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.close()


def test_a4_spinbox_updates_engine(qapp):
    window = MainWindow(FakeAudioInput(tone(440.0, 0.05)))
    window._a4_spin.setValue(442)
    assert window._engine.a4_hz == 442.0
    window.close()
