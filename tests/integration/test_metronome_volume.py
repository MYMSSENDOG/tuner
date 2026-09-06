"""The metronome's loudness, as a bar beside the tempo.

Volume is the click's peak amplitude, so the control means something absolute:
0 is silent, 1 is full scale, and the default 0.5 leaves room either way.

The one non-obvious consequence is at the bottom of this file. Suppression
works by *listening* for the click (core/interference.py), so turning the
volume down turns suppression off by itself — there is no click for the tuner
to find, and nothing to freeze the display for.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtGui import QEnterEvent, QMouseEvent, QPointingDevice

from tests.fakes import FakeAudioInput, FakeAudioOutput
from tests.synth import SR, tone
from tuner.app.metronome import MetronomeService
from tuner.app.metronome_widget import SoundDialog, VolumeBar, volume_from_x
from tuner.core.metronome import (
    CLICK_AMPLITUDE,
    CLICK_SOUNDS,
    MAX_VOLUME,
    MIN_VOLUME,
    Metronome,
)

WIDTH = 44


def drag_to(bar: VolumeBar, x: int) -> None:
    position = QPointF(x, bar.height() / 2)
    for press in (True, False):
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress if press else QMouseEvent.Type.MouseMove,
            position,
            position,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPointingDevice.primaryPointingDevice(),
        )
        bar.mousePressEvent(event) if press else bar.mouseMoveEvent(event)


def hover(bar: VolumeBar) -> None:
    point = QPointF(bar.width() / 2, bar.height() / 2)
    bar.enterEvent(QEnterEvent(point, point, point))


# --- the arithmetic ------------------------------------------------------


def test_volume_from_x_spans_the_bar():
    assert volume_from_x(0, WIDTH) == MIN_VOLUME
    assert volume_from_x(WIDTH - 1, WIDTH) == MAX_VOLUME


def test_volume_from_x_is_clamped():
    assert volume_from_x(-20, WIDTH) == MIN_VOLUME
    assert volume_from_x(WIDTH * 5, WIDTH) == MAX_VOLUME


def test_volume_from_x_snaps_to_five_percent():
    """44 pixels cannot express more, and a readout that says 63% because of
    one pixel is noise pretending to be a setting."""
    values = sorted({volume_from_x(x, WIDTH) for x in range(WIDTH)})
    assert all(abs(v * 20 - round(v * 20)) < 1e-9 for v in values)
    assert len(values) <= 21


# --- the sound -----------------------------------------------------------


def test_volume_is_the_click_amplitude():
    for volume in (0.25, CLICK_AMPLITUDE, 1.0):
        block = Metronome(SR, 120.0, volume=volume).render(SR // 4)
        assert float(np.max(np.abs(block))) == pytest.approx(volume, abs=0.02)


def test_the_default_is_what_it_always_was():
    """The click was 0.5 peak before volume existed; the default must not
    quietly change the sound of every existing install."""
    assert Metronome(SR, 120.0).volume == CLICK_AMPLITUDE
    assert np.array_equal(
        Metronome(SR, 120.0).render(SR),
        Metronome(SR, 120.0, volume=CLICK_AMPLITUDE).render(SR),
    )


def test_silent_is_actually_silent():
    assert not np.any(Metronome(SR, 120.0, volume=0.0).render(SR))


def test_volume_changes_without_a_restart():
    """Finding the right level means turning it down while it plays."""
    output = FakeAudioOutput(sr=SR)
    service = MetronomeService(output, bpm=120.0)
    service.start()
    loud = float(np.max(np.abs(output.pull(SR // 4))))

    service.set_volume(0.1)
    assert output.start_count == 1 and output.stop_count == 0
    quiet = float(np.max(np.abs(output.pull(SR // 4))))
    assert quiet < loud / 2
    service.stop()


def test_volume_is_clamped_and_survives_a_stop():
    service = MetronomeService(FakeAudioOutput(sr=SR))
    assert service.set_volume(5.0) == MAX_VOLUME
    assert service.set_volume(-1.0) == MIN_VOLUME
    service.set_volume(0.3)
    service.start()
    service.stop()
    assert service.volume == 0.3


# --- the widget and the window -------------------------------------------


def test_dragging_reports_the_new_volume(qapp):
    bar = VolumeBar(CLICK_AMPLITUDE)
    seen: list[float] = []
    bar.volume_changed.connect(seen.append)

    drag_to(bar, 0)
    drag_to(bar, WIDTH - 1)
    assert seen == [MIN_VOLUME, MAX_VOLUME]
    assert bar.volume == MAX_VOLUME


def test_set_volume_does_not_echo_back(qapp):
    bar = VolumeBar(CLICK_AMPLITUDE)
    seen: list[float] = []
    bar.volume_changed.connect(seen.append)
    bar.set_volume(0.2)
    assert bar.volume == 0.2 and seen == []


def test_hovering_shows_a_percentage(qapp):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    host = QWidget()
    host.resize(200, 60)
    QHBoxLayout(host).addWidget(bar := VolumeBar(CLICK_AMPLITUDE))
    host.show()

    assert bar._readout.label is None
    hover(bar)
    assert bar._readout.label.text() == "50%"
    assert bar._readout.label.parentWidget() is host
    bar.leaveEvent(None)
    assert not bar._readout.visible
    host.close()


@pytest.fixture
def make_window(qapp, tmp_path):
    from tuner.app.main_window import MainWindow

    def factory():
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        return MainWindow(
            FakeAudioInput(tone(440.0, 0.05)), settings, FakeAudioOutput()
        )

    return factory


def test_the_bar_sits_beside_the_tempo(make_window):
    """'bpm 설정하는 부분 옆에 작게' — after the + button, and small."""
    window = make_window()
    row = window._metronome_bar.layout()
    widgets = [row.itemAt(i).widget() for i in range(row.count())]
    order = [w for w in widgets if w is not None]
    bar = window._metronome_bar._volume
    assert order.index(bar) == order.index(window._metronome_bar._up) + 1
    assert bar.width() <= 48
    window.close()


def test_dragging_reaches_the_metronome(make_window):
    window = make_window()
    drag_to(window._metronome_bar._volume, 0)
    assert window._metronome.volume == MIN_VOLUME
    window.close()


def test_volume_survives_a_restart(make_window):
    window = make_window()
    window._metronome_bar.set_volume(0.25)
    window.close()

    restored = make_window()
    assert restored._metronome.volume == pytest.approx(0.25)
    assert restored._metronome_bar._volume.volume == pytest.approx(0.25)
    restored.close()


def test_the_window_did_not_get_wider(make_window):
    """The metronome row must not become the widest thing in the window; the
    controls row's 294px is the floor and this feature does not spend it."""
    window = make_window()
    assert window.minimumSizeHint().width() <= 294
    window.close()


# --- what turning it down means for the tuner ----------------------------


def test_a_quiet_click_stops_being_suppressed():
    """Not a rule anyone wrote: the tuner finds the beat by listening, so a
    click the microphone cannot hear is one it will not freeze for. Turning
    the volume down hands the display back on its own."""
    from tests.integration.test_metronome_interference import (
        heard,
        mix_clicks,
        run,
    )

    played = tone(440.0, 6.0, instrument="violin")
    for level, expect_locked in ((0.5, True), (0.0005, False)):
        signal, _ = mix_clicks(played, SR, 120.0, level=level)
        source = heard(120.0)
        run(signal, SR, source)
        assert source.inner.locked is expect_locked
        if not expect_locked:
            assert source.frozen == 0


# --- choosing the sound --------------------------------------------------


def test_the_sound_button_shows_what_will_play(make_window):
    window = make_window()
    bar = window._metronome_bar
    assert bar._sound.text() == window._metronome.sound
    bar.set_sound("틱")
    assert bar._sound.text() == "틱" and window._metronome.sound == "틱"
    window.close()


def test_the_row_order_is_play_tempo_volume_sound(make_window):
    """Volume stays next to the tempo, as asked; the sound button follows it."""
    window = make_window()
    bar = window._metronome_bar
    row = bar.layout()
    order = [row.itemAt(i).widget() for i in range(row.count())]
    order = [w for w in order if w is not None]
    assert order.index(bar._volume) == order.index(bar._up) + 1
    assert order.index(bar._sound) == order.index(bar._volume) + 1
    window.close()


def test_the_dialog_lists_every_sound_and_starts_on_the_current_one(qapp):
    from tuner.app.metronome_widget import SoundDialog

    service = MetronomeService(FakeAudioOutput(sr=SR), sound="비프")
    dialog = SoundDialog(service)
    listed = [dialog._list.item(i).text() for i in range(dialog._list.count())]
    assert listed == list(CLICK_SOUNDS)
    assert dialog.chosen == "비프"
    dialog.reject()


def test_moving_the_selection_auditions_it(qapp):
    """Hearing is what decides, so selection plays rather than waiting for OK."""
    output = FakeAudioOutput(sr=SR)
    service = MetronomeService(output, sound="클릭")
    dialog = SoundDialog(service)

    dialog._list.setCurrentRow(list(CLICK_SOUNDS).index("틱"))
    assert service.sound == "틱"
    assert service.previewing and output.start_count == 1
    played = output.pull(int(SR * 0.05))
    assert float(np.max(np.abs(played))) > 0.1  # it was actually audible
    dialog.reject()


def test_cancel_puts_back_the_sound_you_came_in_with(qapp):
    """An audition must not be able to change the setting by accident."""
    service = MetronomeService(FakeAudioOutput(sr=SR), sound="클릭")
    dialog = SoundDialog(service)
    dialog._list.setCurrentRow(list(CLICK_SOUNDS).index("우드블록"))
    assert service.sound == "우드블록"

    dialog.reject()
    assert service.sound == "클릭"
    assert not service.previewing


def test_ok_keeps_the_audition(qapp):
    service = MetronomeService(FakeAudioOutput(sr=SR), sound="클릭")
    dialog = SoundDialog(service)
    dialog._list.setCurrentRow(list(CLICK_SOUNDS).index("우드블록"))
    dialog.accept()
    assert service.sound == "우드블록"
    assert not service.previewing


def test_the_audition_releases_the_device(qapp):
    """One stream per audition, closed when it has been heard — clicking down
    the list must not leave a backlog of open devices."""
    output = FakeAudioOutput(sr=SR)
    service = MetronomeService(output, sound="클릭")
    dialog = SoundDialog(service)
    for name in CLICK_SOUNDS:
        dialog._list.setCurrentRow(list(CLICK_SOUNDS).index(name))
    dialog.reject()
    assert output.start_count == output.stop_count


def test_a_running_beat_keeps_the_device_through_an_audition(qapp):
    """While the beat runs the audition *is* the next beat; the device must
    not be torn down under it."""
    output = FakeAudioOutput(sr=SR)
    service = MetronomeService(output, bpm=120.0)
    service.start()
    dialog = SoundDialog(service)
    dialog._list.setCurrentRow(list(CLICK_SOUNDS).index("틱"))

    assert not service.previewing
    assert output.start_count == 1 and output.stop_count == 0
    assert len(output.pull(256)) == 256
    dialog.accept()
    service.stop()


def test_the_sound_survives_a_restart(make_window):
    window = make_window()
    window._metronome_bar.set_sound("우드블록")
    window.close()

    restored = make_window()
    assert restored._metronome.sound == "우드블록"
    assert restored._metronome_bar._sound.text() == "우드블록"
    restored.close()
