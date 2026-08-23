"""The metronome row in the window: where it sits, what it drives, what it
persists. Offscreen, with a fake output device — nothing here should need a
sound card, and anything that does would be untestable in CI."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings

from tests.fakes import FakeAudioInput, FakeAudioOutput
from tests.synth import tone

# The floor set by the controls row (A4 + device + pin). The metronome row is
# under it and must not become the widest thing in the window - the compact
# window was measured at 294px and this feature is not allowed to spend it.
MAX_WINDOW_WIDTH = 294


@pytest.fixture
def make_window(qapp, tmp_path):
    from tuner.app.main_window import MainWindow

    def factory(output=None):
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        return MainWindow(
            FakeAudioInput(tone(440.0, 0.05)), settings, output or FakeAudioOutput()
        )

    return factory


def test_row_sits_between_the_controls_and_the_meter(make_window):
    """Where the user asked for it: under A4/device/pin, above the display."""
    window = make_window()
    layout = window.centralWidget().layout()
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order.index(window._metronome_bar) == 1  # 0 is the controls row
    assert order.index(window._metronome_bar) < order.index(window._meter)
    window.close()


def test_the_row_does_not_widen_the_window(make_window):
    window = make_window()
    assert window.minimumSizeHint().width() <= MAX_WINDOW_WIDTH
    window.close()


def test_play_button_drives_the_device(make_window):
    output = FakeAudioOutput()
    window = make_window(output)
    bar = window._metronome_bar

    bar._play.click()
    assert output.start_count == 1 and window._metronome.running
    assert bar._play.text() == "■"

    bar._play.click()
    assert output.stop_count == 1 and not window._metronome.running
    assert bar._play.text() == "▶"
    window.close()


def test_nudge_buttons_change_tempo_in_place(make_window):
    """The +/- buttons must not open anything: they are the answer to 'a bit
    faster', and a dialog for that would be worse than no button."""
    window = make_window()
    bar = window._metronome_bar
    start = window._metronome.bpm

    bar._up.click()
    assert window._metronome.bpm == start + 1
    bar._down.click()
    bar._down.click()
    assert window._metronome.bpm == start - 1
    assert bar._bpm.text() == f"{start - 1:g}"
    window.close()


def test_tempo_is_clamped_at_the_button(make_window):
    """Holding '-' at the bottom of the range must stop, not wrap or go mad."""
    from tuner.core.metronome import MIN_BPM

    window = make_window()
    window._metronome_bar.set_bpm(MIN_BPM)
    for _ in range(5):
        window._metronome_bar._down.click()
    assert window._metronome.bpm == MIN_BPM
    assert window._metronome_bar._bpm.text() == f"{MIN_BPM:g}"
    window.close()


def test_tempo_survives_a_restart(make_window):
    window = make_window()
    window._metronome_bar.set_bpm(138.0)
    window.close()

    restored = make_window()
    assert restored._metronome.bpm == 138.0
    assert restored._metronome_bar._bpm.text() == "138"
    restored.close()


def test_closing_the_window_releases_the_output_device(make_window):
    output = FakeAudioOutput()
    window = make_window(output)
    window._metronome_bar._play.click()
    window.close()
    assert output.stop_count == 1


def test_the_tuner_is_told_about_this_metronome(make_window):
    """The wiring that makes suppression possible at all: the engine's
    interference source must be *this* metronome's timeline, not a new one."""
    window = make_window()
    assert window._engine._interference is window._metronome.clicks
    window.close()


def test_no_output_device_is_reported_not_raised(make_window):
    """A machine with no speakers is ordinary. Pressing play there must leave
    the tuner half of the window working and say why, not throw out of a
    button press."""

    class DeadOutput(FakeAudioOutput):
        def start(self, render):
            raise RuntimeError("출력 장치가 없다")

    window = make_window(DeadOutput())
    window._metronome_bar._play.click()

    assert not window._metronome.running
    assert window._metronome_bar._play.text() == "▶"
    assert not window._note.isHidden()  # the user was told
    window.close()
