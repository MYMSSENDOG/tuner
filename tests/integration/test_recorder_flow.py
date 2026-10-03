"""Press record, play, press stop, name it, see it in the list — offscreen.

The pieces are tested on their own (tests/unit/test_recorder.py for the files,
test_recorder_ui.py for the geometry); this is the path a player actually
walks, including the two places it can be abandoned.

The save dialog is modal, so every test here replaces its `exec` rather than
driving it — a test that opened a real modal would hang the suite.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from tests.fakes import FakeAudioInput, FakeAudioOutput
from tests.synth import tone
from tuner.app import main_window as mw
from tuner.app import recorder


@pytest.fixture
def takes_dir(tmp_path, monkeypatch):
    directory = tmp_path / "takes"
    monkeypatch.setenv(recorder.ENV_DIR, str(directory))
    return directory


@pytest.fixture
def make_window(qapp, tmp_path, takes_dir):
    created = []

    def factory(seconds: float = 1.2):
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        fake = FakeAudioInput(tone(440.0, seconds, instrument="violin"))
        window = mw.MainWindow(
            fake, settings, FakeAudioOutput(), FakeAudioOutput(), recordings_dir=takes_dir
        )
        window.show()
        window.start()
        qapp.processEvents()
        created.append(window)
        return window, fake

    yield factory
    # Closing the window while a take is running opens the save dialog on
    # purpose - that is the promise that a performance is never dropped. In a
    # test that would be a *real* modal with nobody to answer it, so teardown
    # throws the take away first. (Found the hard way: one test left a take
    # running and the suite hung forever on the next one's turn.)
    for window in created:
        window._recorder.discard()
        window.close()


def answer_save_dialog(monkeypatch, title: str | None):
    """Make the next save dialog return `title`, or None to discard."""
    captured = {}

    def fake_exec(self):
        captured["default"] = self.title
        if title is None:
            return QDialog.DialogCode.Rejected
        self._field.setText(title)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(mw.SaveTakeDialog, "exec", fake_exec, raising=False)
    return captured


# --- the whole path -----------------------------------------------------------


def test_record_play_stop_save_appears_in_the_list(make_window, qapp, monkeypatch, takes_dir):
    window, fake = make_window()
    captured = answer_save_dialog(monkeypatch, "바흐 프렐류드")

    window.toggle_recording()
    fake.pump()
    qapp.processEvents()
    assert window.recording

    path = window.stop_recording()
    qapp.processEvents()

    assert window._drawer.status_text, "the drawer says what happened"
    assert path is not None and path.exists()
    assert path.parent == takes_dir
    assert recorder.is_ours(path)
    assert window._drawer.take_count == 1
    assert [t.title for t in window._takes] == ["바흐 프렐류드"]
    # the dialog offered the date and time before it was renamed
    assert captured["default"] == recorder.default_title()


def test_the_take_carries_the_reference_pitch_that_was_set(make_window, qapp, monkeypatch):
    window, fake = make_window()
    answer_save_dialog(monkeypatch, "기준 442")
    window._a4_spin.setValue(442)

    window.toggle_recording()
    fake.pump()
    path = window.stop_recording()
    qapp.processEvents()

    import json

    import soundfile as sf

    with sf.SoundFile(str(path)) as handle:
        assert json.loads(handle.comment)["a4_hz"] == pytest.approx(442.0)


def test_discarding_writes_nothing(make_window, qapp, monkeypatch, takes_dir):
    window, fake = make_window()
    answer_save_dialog(monkeypatch, None)

    window.toggle_recording()
    fake.pump()
    assert window.stop_recording() is None
    qapp.processEvents()

    assert list(takes_dir.glob("*.ogg")) == [] if takes_dir.exists() else True
    assert window._drawer.take_count == 0


def test_a_take_too_short_to_be_a_performance_is_not_offered(make_window, qapp, monkeypatch):
    """Two presses in a row. Saying so beats opening a dialog about nothing."""
    window, _ = make_window()
    opened = []
    monkeypatch.setattr(
        mw.SaveTakeDialog, "exec", lambda self: opened.append(1), raising=False
    )
    window.toggle_recording()
    assert window.stop_recording() is None
    assert opened == []


def test_pausing_shortens_the_saved_take(make_window, qapp, monkeypatch):
    window, fake = make_window(seconds=1.0)
    answer_save_dialog(monkeypatch, "일시정지한 것")

    window.toggle_recording()
    fake.pump()  # 1.0s recorded
    window.toggle_pause()
    fake.pump()  # ignored
    window.toggle_pause()
    fake.pump()  # 1.0s more
    qapp.processEvents()
    path = window.stop_recording()

    import soundfile as sf

    assert sf.info(str(path)).duration == pytest.approx(2.0, abs=0.05)


def test_the_elapsed_clock_follows_what_was_recorded(make_window, qapp, monkeypatch):
    window, fake = make_window(seconds=0.75)
    answer_save_dialog(monkeypatch, None)  # this take is not the point
    window.toggle_recording()
    fake.pump()
    window._tick_recorder()
    qapp.processEvents()
    assert window._drawer.elapsed_text == "00:00:00"  # 0.75s rounds down
    fake.pump()
    window._tick_recorder()
    assert window._drawer.elapsed_text == "00:00:01"


def test_closing_mid_take_saves_rather_than_drops(make_window, qapp, monkeypatch, takes_dir):
    """A performance is never lost on the way out — the same promise the
    report session already makes."""
    window, fake = make_window()
    answer_save_dialog(monkeypatch, "닫는 중")
    window.toggle_recording()
    fake.pump()
    qapp.processEvents()
    window.close()
    assert [p.stem for p in takes_dir.glob("*.ogg")] == ["닫는 중"]


# --- the list's own buttons ------------------------------------------------------


def saved(window, fake, qapp, monkeypatch, title):
    answer_save_dialog(monkeypatch, title)
    window.toggle_recording()
    fake.pump()
    path = window.stop_recording()
    qapp.processEvents()
    return path


def test_rename_changes_the_title_and_the_file(make_window, qapp, monkeypatch):
    window, fake = make_window()
    path = saved(window, fake, qapp, monkeypatch, "원래")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("새 제목", True)))

    window.rename_selected()
    qapp.processEvents()
    assert not path.exists()
    assert [t.title for t in window._takes] == ["새 제목"]


def test_cancelling_a_rename_changes_nothing(make_window, qapp, monkeypatch):
    window, fake = make_window()
    saved(window, fake, qapp, monkeypatch, "그대로")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    window.rename_selected()
    assert [t.title for t in window._takes] == ["그대로"]


def test_delete_asks_first(make_window, qapp, monkeypatch):
    window, fake = make_window()
    saved(window, fake, qapp, monkeypatch, "지울까")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel),
    )
    window.delete_selected()
    assert window._drawer.take_count == 1


def test_everything_the_recorder_says_is_said_in_the_drawer(make_window, qapp, monkeypatch):
    """The tuner's own note line stays about the tuner."""
    window, _ = make_window()
    opened = []
    monkeypatch.setattr(mw.SaveTakeDialog, "exec", lambda self: opened.append(1), raising=False)
    window.toggle_recording()
    window.stop_recording()  # too short to be offered
    qapp.processEvents()
    assert "짧" in window._drawer.status_text
    assert window._note.isHidden(), "the tuner's line was not used"


def test_delete_removes_the_file_once_confirmed(make_window, qapp, monkeypatch):
    window, fake = make_window()
    path = saved(window, fake, qapp, monkeypatch, "지운다")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard),
    )
    window.delete_selected()
    qapp.processEvents()
    assert not path.exists()
    assert window._drawer.take_count == 0


def test_the_buttons_do_nothing_with_an_empty_list(make_window, qapp, monkeypatch):
    """Nothing selected means nothing to act on — and no exception either."""
    window, _ = make_window()
    assert window._drawer.take_count == 0
    window.open_take(0)
    window.toggle_playback()
    window.rename_selected()
    window.delete_selected()
    assert not window._player.playing
    assert not window._player.loaded


# --- playback ---------------------------------------------------------------------


def test_opening_a_take_loads_it_without_starting_it(make_window, qapp, monkeypatch):
    """The row you clicked is the one you meant to look at, which is not always
    the one you meant to hear — the same as the Windows recorder."""
    window, fake = make_window()
    saved(window, fake, qapp, monkeypatch, "들어보자")
    window.open_take(0)
    qapp.processEvents()
    assert window._drawer.page == 2  # PLAYBACK
    assert window._player.loaded
    assert not window._player.playing
    assert window._drawer.play_glyph == "play"


def test_the_play_button_toggles(make_window, qapp, monkeypatch):
    window, fake = make_window()
    saved(window, fake, qapp, monkeypatch, "들어보자")
    window.open_take(0)

    window.toggle_playback()
    assert window._player.playing
    assert window._drawer.play_glyph == "pause"

    window.toggle_playback()
    assert not window._player.playing
    assert window._player.loaded, "pausing keeps the take in hand"
    assert window._drawer.play_glyph == "play"


def test_going_back_gives_the_device_up(make_window, qapp, monkeypatch):
    window, fake = make_window()
    saved(window, fake, qapp, monkeypatch, "돌아가기")
    window.open_take(0)
    window.toggle_playback()
    window.close_take()
    qapp.processEvents()
    assert not window._player.loaded
    assert window._drawer.page == 0  # LIST


def test_seeking_moves_the_position(make_window, qapp, monkeypatch):
    window, fake = make_window(seconds=2.0)
    saved(window, fake, qapp, monkeypatch, "탐색")
    window.open_take(0)
    total = window._player.duration_seconds
    assert total > 1.0

    window._seek_fraction(0.5)
    assert window._player.position_seconds == pytest.approx(total / 2, abs=0.05)


def test_recording_stops_whatever_was_playing(make_window, qapp, monkeypatch):
    """Listening and recording at once would put the playback into the take."""
    window, fake = make_window()
    saved(window, fake, qapp, monkeypatch, "재생 중")
    window.open_take(0)
    window.toggle_playback()
    assert window._player.playing

    window.toggle_recording()
    assert not window._player.playing
    assert not window._player.loaded
    assert window.recording


# --- what is remembered -------------------------------------------------------------


def test_the_drawer_comes_back_the_way_it_was_left(make_window, qapp):
    window, _ = make_window()
    window.set_drawer_open(True)
    window.close()
    qapp.processEvents()

    reopened, _ = make_window()
    assert reopened.drawer_open


def test_a_closed_drawer_stays_closed(make_window, qapp):
    window, _ = make_window()
    assert not window.drawer_open
    window.close()
    reopened, _ = make_window()
    assert not reopened.drawer_open


def test_takes_are_found_again_on_the_next_run(make_window, qapp, monkeypatch):
    window, fake = make_window()
    saved(window, fake, qapp, monkeypatch, "지난번 녹음")
    window.close()

    reopened, _ = make_window()
    assert [t.title for t in reopened._takes] == ["지난번 녹음"]
    assert reopened._drawer.take_count == 1
