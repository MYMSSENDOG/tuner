"""The recorder's panel, offscreen: the handle, the pages, the state.

What this has to get right is geometry and which page is showing. The files
are tested in tests/unit/test_recorder.py and the whole path a player walks is
in test_recorder_flow.py.

The promise the drawer is built on: the tuner column is exactly what it was
before the recorder existed. Nothing was added to its controls row, and the
handle is drawn over its edge rather than beside it, so closed the window is
the same 310px it has always been.
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
from tuner.app.drawer_handle import HIT_MARGIN
from tuner.app.drawer_handle import WIDTH as HANDLE_WIDTH
from tuner.app.recordings_drawer import WIDTH as DRAWER_WIDTH
from tuner.app.recordings_drawer import RecordingsDrawer, clock_text, short_clock


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("TUNER_RECORDINGS_DIR", str(tmp_path / "takes"))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = mw.MainWindow(
        FakeAudioInput(tone(440.0, 0.2, instrument="violin")),
        settings,
        FakeAudioOutput(),
        FakeAudioOutput(),
    )
    w.show()
    w.start()  # the fake only delivers blocks once the engine has subscribed
    qapp.processEvents()
    yield w
    w._recorder.discard()  # closing mid-take opens a modal nobody can answer
    w.close()


def tuner_column(window):
    return window.centralWidget().layout().itemAt(0).widget()


# --- the tuner is untouched ---------------------------------------------------


def test_the_closed_window_is_the_width_it_always_was(window):
    """The recorder put nothing on the controls row and the handle is drawn
    over the column rather than beside it, so the floor is unchanged.
    It briefly was not: a dot on the row plus a handle in the layout took the
    window to 331.
    """
    assert not window.drawer_open
    assert window.width() == mw.TUNER_WIDTH
    assert window.minimumSizeHint().width() <= 294


def test_the_controls_row_holds_only_the_tuners_own_controls(window):
    row = tuner_column(window).layout().itemAt(0)
    widgets = [row.itemAt(i).widget() for i in range(row.count())]
    kinds = [w.__class__.__name__ for w in widgets if w is not None]
    assert kinds == ["QLabel", "QSpinBox", "_DeviceComboBox", "QCheckBox"]


def test_opening_the_drawer_does_not_narrow_the_tuner(window, qapp):
    """The meter keeps every pixel it had."""
    before = window._meter.width()
    window.set_drawer_open(True)
    qapp.processEvents()
    assert window._meter.width() == before
    assert tuner_column(window).width() == mw.TUNER_WIDTH


def test_the_drawer_only_adds_width(window, qapp):
    """Opening moves the right edge and nothing else. The window used to be
    lent 360px of height as well, and the tuner jumped taller with it."""
    width, height = window.width(), window.height()
    window.set_drawer_open(True)
    qapp.processEvents()
    assert (window.width(), window.height()) == (width + DRAWER_WIDTH, height)

    window.set_drawer_open(False)
    qapp.processEvents()
    assert (window.width(), window.height()) == (width, height)


def test_a_taller_window_keeps_its_height_too(window, qapp):
    window.resize(window.width(), 480)
    qapp.processEvents()
    tall = window.height()
    window.set_drawer_open(True)
    qapp.processEvents()
    assert window.height() == tall
    window.set_drawer_open(False)
    qapp.processEvents()
    assert window.height() == tall


def _drawer_states(drawer):
    """Every page in its tallest state, each with a message under it."""
    yield "list", drawer.show_list
    yield "recording", drawer.show_recording

    def paused():
        drawer.show_recording()
        drawer.set_paused(True)

    yield "paused", paused
    yield "playback", lambda: drawer.show_playback("2026-10-05 15-15", "2026-10-05 오후 3:15", 154.0)


@pytest.mark.parametrize("state", ["list", "recording", "paused", "playback"])
def test_every_page_fits_the_tuner_at_its_shortest(window, qapp, state):
    """What lets the window keep its height: no page, with a message showing,
    asks for more height than the tuner's own floor. A page that did would
    make Qt grow the window the moment it appeared."""
    drawer = window._drawer
    window.set_drawer_open(True)
    dict(_drawer_states(drawer))[state]()
    drawer.say("녹음 저장 실패: " + "아주 긴 오류 메시지 " * 8, ok=False)
    qapp.processEvents()
    floor = tuner_column(window).minimumSizeHint().height()
    assert drawer.minimumSizeHint().height() <= floor


def test_a_long_message_stays_one_line_but_is_kept_whole(window, qapp):
    drawer = window._drawer
    message = "녹음 저장 실패: " + "아주 긴 오류 메시지 " * 8
    drawer.say(message, ok=False)
    assert drawer.status_text == message
    assert drawer._status.text() != message  # elided on screen


def test_the_handle_is_the_only_thing_always_on_screen(window):
    assert not window.drawer_open
    assert window._drawer_handle.isVisible()
    assert window._drawer_handle.width() == HANDLE_WIDTH
    # drawn over the column's right edge, not taking a column of its own
    assert window._drawer_handle.parent() is tuner_column(window)
    assert window._drawer_handle.x() == mw.TUNER_WIDTH - HANDLE_WIDTH


# --- the handle ---------------------------------------------------------------


def test_clicking_the_handle_opens_and_closes(window, qapp):
    handle = window._drawer_handle

    def click_the_tab():
        # recomputed each time: the tab sits at the column's vertical centre,
        # and opening the drawer makes the window taller, so the point that
        # was on the tab a moment ago is not any more
        QTest.mouseClick(
            handle,
            Qt.MouseButton.LeftButton,
            pos=QPoint(HANDLE_WIDTH // 2, handle.height() // 2),
        )
        qapp.processEvents()

    click_the_tab()
    assert window.drawer_open
    click_the_tab()
    assert not window.drawer_open


def test_the_handle_covers_only_its_tab(window, qapp):
    """Drawn over the column's edge, it must not own that edge.

    It used to be a full-height strip: everything under the rightmost 11px of
    the column - the last ~2dB of the input gate's travel on the level bar, the
    end of the trace - was the handle's, not theirs. Widgets underneath are
    siblings, so no event ever passed through to them.
    """
    column = tuner_column(window)
    handle = window._drawer_handle
    assert handle.height() <= HANDLE_HEIGHT + 2 * HIT_MARGIN

    edge_x = column.width() - HANDLE_WIDTH // 2 - 1
    for widget in (window._level_bar, window._trace):
        point = widget.mapTo(column, QPoint(widget.width() - 3, widget.height() // 2))
        assert column.childAt(point) is not handle, (
            f"{type(widget).__name__}'s right edge is under the handle"
        )
    top = QPoint(edge_x, 2)
    assert column.childAt(top) is not handle


def test_the_tab_stays_centred_as_the_window_grows(window, qapp):
    column = tuner_column(window)
    handle = window._drawer_handle
    window.set_drawer_open(True)
    qapp.processEvents()
    centre = handle.y() + handle.height() // 2
    assert abs(centre - column.height() // 2) <= 1


def test_the_handle_opens_the_list_not_a_recording(window, qapp):
    window.toggle_drawer()
    qapp.processEvents()
    assert window.drawer_open
    assert not window.recording
    assert window._drawer.page == RecordingsDrawer.LIST


# --- which page is showing -----------------------------------------------------


def test_the_mic_starts_a_take_and_shows_the_recording_page(window, qapp):
    window._drawer.record_pressed.emit()
    qapp.processEvents()
    assert window.recording
    assert window.drawer_open
    assert window._drawer.page == RecordingsDrawer.RECORDING


def test_stopping_goes_back_to_the_list(window, qapp, monkeypatch):
    monkeypatch.setattr(mw.SaveTakeDialog, "exec", lambda self: 0, raising=False)
    window.toggle_recording()
    qapp.processEvents()
    window.stop_recording()
    qapp.processEvents()
    assert not window.recording
    assert window._drawer.page == RecordingsDrawer.LIST


def test_pausing_turns_the_page_into_a_place_to_listen(window, qapp):
    """Paused: the button under the circle offers to carry on *recording*
    (it used to be a play triangle - the same mark as "listen", for the
    opposite action), the rings come off, and the small player appears."""
    window.toggle_recording()
    window.toggle_pause()
    qapp.processEvents()
    assert window.paused
    assert "이어서 녹음" in window._drawer.pause_text
    assert "▶" not in window._drawer.pause_text
    assert window._drawer.listen_visible
    window.toggle_pause()
    assert not window.paused
    assert "일시정지" in window._drawer.pause_text
    assert not window._drawer.listen_visible


def test_pause_and_stop_do_nothing_when_nothing_is_recording(window):
    """An inapplicable key says nothing rather than complaining — this app
    does not interrupt a player to tell them what they cannot do."""
    assert not window.recording
    window.toggle_pause()
    window.stop_recording()
    assert not window.recording
    assert not window.paused
    assert window._drawer.page == RecordingsDrawer.LIST


# --- the level trail ------------------------------------------------------------


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


def test_the_trail_stands_still_while_paused(window, qapp):
    """Nothing is being recorded, so nothing scrolls. It used to keep moving
    in a dimmer colour, which read as still recording."""
    window.toggle_recording()
    window._engine._audio.pump()
    qapp.processEvents()
    before = window._drawer._trail.bars
    assert before

    window.toggle_pause()
    window._engine._audio.pump()
    qapp.processEvents()
    assert window._drawer._trail.bars == before


# --- clocks ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,shown",
    [(0, "00:00:00"), (9.9, "00:00:09"), (84, "00:01:24"), (3725, "01:02:05")],
)
def test_the_recording_clock_counts_in_three_fields(seconds, shown):
    """Always hh:mm:ss, so the digits do not shift sideways mid-take."""
    assert clock_text(seconds) == shown


@pytest.mark.parametrize(
    "seconds,shown", [(0, "0:00"), (9.9, "0:09"), (84, "1:24"), (3725, "62:05")]
)
def test_durations_read_as_minutes_and_seconds(seconds, shown):
    """The list and the seek bar, where three fields would be noise. Past an
    hour it keeps counting minutes rather than wrapping."""
    assert short_clock(seconds) == shown


# --- the keys ---------------------------------------------------------------------


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


def test_the_record_key_works_with_the_drawer_closed(window, qapp):
    """A player's hands are on the instrument; the key cannot require that a
    panel be opened first."""
    assert not window.drawer_open
    window.toggle_recording()  # what Alt+R is wired to
    qapp.processEvents()
    assert window.recording and window.drawer_open
