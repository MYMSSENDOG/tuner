"""The metronome as the app assembles it: core + output device + click times.

core/metronome.py is already sealed against arithmetic (tests/unit/
test_metronome.py). What is left to check here is the wiring: that the device
gets the samples, and that the tuner is told when each click was *audible*,
which is not the same instant it was rendered.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.fakes import FakeAudioOutput
from tests.synth import SR
from tuner.app.metronome import MetronomeService
from tuner.core.metronome import MAX_BPM, MIN_BPM, click_waveform


def service(bpm=120.0, latency_s=0.0, now=None):
    output = FakeAudioOutput(sr=SR, latency_s=latency_s)
    clock = (lambda: now[0]) if now is not None else (lambda: 0.0)
    return MetronomeService(output, bpm=bpm, clock=clock), output


def test_start_and_stop_own_the_device():
    metronome, output = service()
    assert not metronome.running

    metronome.start()
    assert metronome.running and output.start_count == 1
    metronome.start()  # idempotent: a second press must not open a second stream
    assert output.start_count == 1

    metronome.stop()
    assert not metronome.running and output.stop_count == 1
    metronome.stop()
    assert output.stop_count == 1


def test_toggle_reports_the_state_it_left():
    metronome, _ = service()
    assert metronome.toggle() is True
    assert metronome.toggle() is False


def test_the_device_gets_clicks():
    metronome, output = service(bpm=120.0)
    metronome.start()
    block = output.pull(SR)  # one second at 120 BPM = beats at 0 and 0.5s
    loud = np.flatnonzero(np.abs(block) > 0.05)
    assert loud[0] <= 2
    assert any(abs(i - SR // 2) <= 2 for i in loud)


def test_the_tuner_is_told_the_tempo_and_nothing_else():
    """The whole prior handed to suppression. Not when we played — that is
    what the microphone is for (core/interference.py)."""
    metronome, _ = service(bpm=120.0)
    assert metronome.clicks._period is None  # nothing running, nothing to find

    metronome.start()
    assert metronome.clicks._period == pytest.approx(0.5)
    metronome.set_bpm(200.0)
    assert metronome.clicks._period == pytest.approx(0.3)

    metronome.stop()
    assert metronome.clicks._period is None


def test_a_stopped_metronome_suppresses_nothing():
    """Whatever it had locked onto, it must let go of: the next sound at that
    phase is the instrument."""
    metronome, output = service(bpm=120.0)
    metronome.start()
    for _ in range(8):
        metronome.clicks.observe(output.pull(256), 0.0, SR)
    metronome.stop()
    assert not metronome.clicks.locked
    assert not metronome.clicks.contaminates(0.0, 10.0)


def test_tempo_change_does_not_interrupt_the_stream():
    """Turning the dial mid-bar must not stop and restart the device: that
    would gap the audio for a tempo change that core/metronome.py already
    handles by rebasing."""
    metronome, output = service(bpm=120.0)
    metronome.start()
    output.pull(SR // 4)
    metronome.set_bpm(90.0)
    assert output.start_count == 1 and output.stop_count == 0
    assert metronome.bpm == 90.0
    assert len(output.pull(SR)) == SR


def test_tempo_is_clamped_and_the_taken_value_returned():
    """The UI shows what was taken, so a rejected value must never be silently
    different from what is being played."""
    metronome, _ = service()
    assert metronome.set_bpm(0.0) == MIN_BPM
    assert metronome.set_bpm(10_000.0) == MAX_BPM
    assert metronome.bpm == MAX_BPM


def test_tempo_survives_a_stop():
    metronome, _ = service(bpm=90.0)
    metronome.set_bpm(144.0)
    metronome.start()
    metronome.stop()
    assert metronome.bpm == 144.0


def test_restart_begins_on_a_beat():
    """Pressing play always starts with a click, rather than resuming the
    phase of the bar that was abandoned."""
    metronome, output = service(bpm=120.0)
    metronome.start()
    output.pull(SR // 3)  # stop between beats
    metronome.stop()
    metronome.start()
    assert np.max(np.abs(output.pull(len(click_waveform(SR))))) > 0.05


def test_a_stopped_metronome_renders_silence():
    """The device may drain a block after stop(); it must not fault or click."""
    metronome, output = service()
    metronome.start()
    render = output._render
    metronome.stop()
    assert not np.any(render(256))
