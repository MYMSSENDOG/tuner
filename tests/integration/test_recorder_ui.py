"""The recorder's controls, offscreen: the dot, the handle, and the drawer.

No audio is written yet — this is the UI step, and what it has to get right is
geometry and state. The promise the drawer is built on is that opening it
costs the tuner nothing: the meter does not move, shrink, or change height,
because every pixel the recorder uses is added beside the tuner rather than
taken out of it.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest

from tests.fakes import FakeAudioInput, FakeAudioOutput
from tests.synth import tone
from tuner.app import main_window as mw
from tuner.app.drawer_handle import HEIGHT as HANDLE_HEIGHT
from tuner.app.drawer_handle import WIDTH as HANDLE_WIDTH
from tuner.app.recordings_drawer import WIDTH as DRAWER_WIDTH
from tuner.app.recordings_drawer import elapsed_text


@pytest.fixture
def window(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = mw.MainWindow(
        FakeAudioInput(tone(440.0, 0.2, instrument="violin")), settings, FakeAudioOutput()
    )
    w.show()
    w.start()  # the fake only delivers blocks once the engine has subscribed
    qapp.processEvents()
    yield w
    w.close()


# --- the drawer costs the tuner nothing -------------------------------------


def test_opening_the_drawer_does_not_move_the_meter(window, qapp):
    """The whole point of hanging it off the right edge."""
    before = (window._meter.width(), window._meter.height())
    window.set_drawer_open(True)
    qapp.processEvents()
    assert (window._meter.width(), window._meter.height()) == before


def test_the_window_grows_by_exactly_the_drawer(window, qapp):
    width, height = window.width(), window.height()
    window.set_drawer_open(True)
    qapp.processEvents()
    assert window.width() == width + DRAWER_WIDTH
    assert window.height() == height

    window.set_drawer_open(False)
    qapp.processEvents()
    assert window.width() == width


def test_recording_does_not_make_the_window_taller(window, qapp):
    """The recording panel appears inside the drawer, which has to absorb it.

    It did not at first: the list refused to go below its own 70px minimum, so
    the panel pushed the whole window from 197 to 207 — the one thing this
    layout exists to prevent.
    """
    window.set_drawer_open(True)
    qapp.processEvents()
    height = window.height()
    window.toggle_recording()
    qapp.processEvents()
    assert window.height() == height
    assert window._drawer.minimumSizeHint().height() <= height


def test_the_handle_is_the_only_thing_always_on_screen(window):
    """Closed, the recorder costs 11px of window and nothing else."""
    assert not window.drawer_open
    assert window._drawer_handle.isVisible()
    assert window._drawer_handle.width() == HANDLE_WIDTH


# --- starting, pausing, stopping ---------------------------------------------


def test_the_dot_starts_a_take_and_opens_the_drawer(window, qapp):
    assert not window.recording
    window._record_button.click()
    qapp.processEvents()
    assert window.recording
    assert window.drawer_open
    assert window._drawer.recording_visible


def test_pressing_the_dot_again_stops(window, qapp):
    window._record_button.click()
    window._record_button.click()
    qapp.processEvents()
    assert not window.recording
    assert not window._drawer.recording_visible
    assert window.drawer_open, "the drawer stays open — the take is in that list"


def test_pause_toggles_and_says_so(window, qapp):
    window.toggle_recording()
    window.toggle_pause()
    qapp.processEvents()
    assert window.paused
    assert "재개" in window._drawer.pause_text
    window.toggle_pause()
    assert not window.paused
    assert "일시정지" in window._drawer.pause_text


def test_pause_and_stop_do_nothing_when_nothing_is_recording(window):
    """An inapplicable key says nothing rather than complaining — this app
    does not interrupt a player to tell them what they cannot do."""
    assert not window.recording
    window.toggle_pause()
    window.stop_recording()
    assert not window.recording
    assert not window.paused
    assert not window._drawer.recording_visible


def test_stopping_clears_the_recording_panel(window, qapp):
    window.toggle_recording()
    qapp.processEvents()
    window.stop_recording()
    qapp.processEvents()
    assert not window._drawer.recording_visible
    assert window._drawer.elapsed_text == "0:00"


# --- the handle ---------------------------------------------------------------


def test_clicking_the_handle_opens_and_closes(window, qapp):
    handle = window._drawer_handle
    middle = QPoint(HANDLE_WIDTH // 2, handle.height() // 2)
    QTest.mouseClick(handle, Qt.MouseButton.LeftButton, pos=middle)
    qapp.processEvents()
    assert window.drawer_open

    QTest.mouseClick(handle, Qt.MouseButton.LeftButton, pos=middle)
    qapp.processEvents()
    assert not window.drawer_open


def test_the_handle_ignores_clicks_far_from_the_tab(window, qapp):
    """It is 44px of tab in a full-height column; the rest is window edge."""
    handle = window._drawer_handle
    QTest.mouseClick(handle, Qt.MouseButton.LeftButton, pos=QPoint(HANDLE_WIDTH // 2, 1))
    qapp.processEvents()
    assert not window.drawer_open
    assert handle.height() > HANDLE_HEIGHT, "otherwise there is no 'far' to test"


def test_the_handle_opens_the_list_without_recording(window, qapp):
    """The second way in: browsing takes is not starting one."""
    window.toggle_drawer()
    qapp.processEvents()
    assert window.drawer_open
    assert not window.recording
    assert not window._drawer.recording_visible


# --- the level trail ----------------------------------------------------------


def test_the_trail_fills_only_while_recording(window, qapp):
    fake = window._engine._audio
    fake.pump()
    qapp.processEvents()
    assert window._drawer._trail.bars == []

    window.toggle_recording()
    fake.pump()
    qapp.processEvents()
    assert window._drawer._trail.bars, "no level reached the drawer while recording"
    assert all(live for _, live in window._drawer._trail.bars)


def test_a_paused_trail_is_marked_as_paused(window, qapp):
    window.toggle_recording()
    window.toggle_pause()
    window._engine._audio.pump()
    qapp.processEvents()
    bars = window._drawer._trail.bars
    assert bars and not any(live for _, live in bars)


# --- small arithmetic ---------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,shown",
    [(0, "0:00"), (9.9, "0:09"), (60, "1:00"), (84, "1:24"), (3725, "62:05")],
)
def test_elapsed_reads_as_minutes_and_seconds(seconds, shown):
    """Past an hour it keeps counting minutes rather than wrapping: a practice
    take is never "1:05" when it was an hour and five minutes."""
    assert elapsed_text(seconds) == shown


# --- the keys -----------------------------------------------------------------


def test_the_recorder_keys_are_registered(window):
    from PySide6.QtGui import QShortcut

    keys = {
        s.key().toString(QKeySequence.SequenceFormat.PortableText)
        for s in window.findChildren(QShortcut)
    }
    assert {
        mw.RECORD_SHORTCUT,
        mw.PAUSE_SHORTCUT,
        mw.STOP_SHORTCUT,
        mw.DRAWER_SHORTCUT,
    } <= keys


def test_the_recorder_keys_work_without_the_drawer_being_open(window, qapp):
    """A player's hands are on the instrument; the keys cannot require that a
    panel be open first."""
    assert not window.drawer_open
    window.toggle_recording()  # what Alt+R is wired to
    qapp.processEvents()
    assert window.recording and window.drawer_open
