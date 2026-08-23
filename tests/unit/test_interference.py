"""Both interference sources, on signals whose answer is known exactly.

The integration suite judges these by what reaches the meter; this file pins
the mechanics underneath — when a lock may form, how accurate the phase is,
and what has to make it let go.
"""

from __future__ import annotations

import numpy as np
import pytest

import tuner.core.interference as interference_module
from tuner.core.interference import (
    LOCK_MIN_BEATS,
    HeardClicks,
    ScheduledClicks,
)
from tuner.core.metronome import click_waveform

SR = 44100
BLOCK = 256


def click_track(bpm: float, seconds: float, sr: int = SR, level: float = 0.5):
    """Clicks over silence — the metronome alone, as a microphone would get it
    while nobody is playing."""
    signal = np.zeros(int(seconds * sr))
    click = click_waveform(sr) * level / 0.5
    period = 60.0 * sr / bpm
    times, k = [], 0
    while round(k * period) + len(click) < len(signal):
        at = round(k * period)
        signal[at : at + len(click)] += click
        times.append(at / sr)
        k += 1
    return signal, times


def feed(source: HeardClicks, signal: np.ndarray, sr: int = SR) -> None:
    """Deliver the signal blockwise with a clock tied to the samples."""
    for start in range(0, len(signal) - BLOCK + 1, BLOCK):
        source.observe(signal[start : start + BLOCK], (start + BLOCK) / sr, sr)


# --- HeardClicks: the one the app runs ----------------------------------


def test_nothing_is_suppressed_without_a_tempo():
    """The metronome is not running, so every transient belongs to the player."""
    source = HeardClicks()
    feed(source, click_track(120.0, 4.0)[0])
    assert not source.locked
    assert not source.contaminates(0.0, 4.0)


@pytest.mark.parametrize("bpm", [40.0, 120.0, 200.0])
def test_the_phase_is_found_in_the_input(bpm):
    """Given only the tempo, the beat's position comes out of the audio to
    within a block — which is what CLICK_LEAD_S is sized for."""
    signal, times = click_track(bpm, 8.0)
    source = HeardClicks()
    source.set_period(60.0 / bpm)
    feed(source, signal)

    assert source.locked
    period = 60.0 / bpm
    phase = source._peak()
    error = min(abs((t - phase + period / 2) % period - period / 2) for t in times)
    assert error <= 0.012, f"{bpm} BPM: phase off by {error * 1000:.1f}ms"


@pytest.mark.parametrize("bpm", [60.0, 200.0])
def test_locking_costs_two_beats(bpm):
    """A lock is not free and must not pretend to be: nothing is suppressed
    until a bar has been seen twice, so the first clicks of a session do reach
    the display."""
    period = 60.0 / bpm
    signal, _ = click_track(bpm, 8.0)
    source = HeardClicks()
    source.set_period(period)

    feed(source, signal[: int(SR * period * (LOCK_MIN_BEATS - 0.5))])
    assert not source.locked
    feed(source, signal[: int(SR * period * (LOCK_MIN_BEATS + 1))])
    assert source.locked


def test_a_metronome_nobody_can_hear_suppresses_nothing():
    """Headphones. The tempo is set, the beat is running, and the microphone
    is getting nothing but room noise — freezing the meter for that would be
    paying for a sound that never arrived."""
    source = HeardClicks()
    source.set_period(0.5)
    rng = np.random.default_rng(0)
    feed(source, rng.normal(0.0, 0.001, int(6 * SR)))
    assert not source.locked
    assert not source.contaminates(0.0, 6.0)


def test_changing_tempo_throws_the_bar_away():
    """The histogram was binned against the old spacing; kept, it would aim
    suppression at a phase that no longer means anything."""
    signal, _ = click_track(120.0, 6.0)
    source = HeardClicks()
    source.set_period(0.5)
    feed(source, signal)
    assert source.locked

    source.set_period(0.4)
    assert not source.locked
    source.set_period(0.4)  # the same tempo again is not a change
    assert not source.locked


def test_stopping_lets_go():
    signal, _ = click_track(120.0, 6.0)
    source = HeardClicks()
    source.set_period(0.5)
    feed(source, signal)
    assert source.locked

    source.idle()
    assert not source.locked
    assert not source.contaminates(0.0, 10.0)


def test_suppression_covers_the_beat_and_not_the_bar():
    """A window around each beat, not a blanket: between beats the tuner has
    to be looking, or the metronome would cost the whole display."""
    signal, times = click_track(120.0, 8.0)
    source = HeardClicks()
    source.set_period(0.5)
    feed(source, signal)

    beat = times[4]
    assert source.contaminates(beat, beat + 0.001)
    assert not source.contaminates(beat + 0.2, beat + 0.25)  # mid-bar


def test_the_switch_turns_it_off(monkeypatch):
    """docs/pitch-pipeline.md lists this as a one-line switch; it has to be
    one, or comparing with and without means editing the algorithm."""
    signal, _ = click_track(120.0, 6.0)
    source = HeardClicks()
    source.set_period(0.5)
    feed(source, signal)
    assert source.contaminates(0.0, 10.0)

    monkeypatch.setattr(interference_module, "CLICK_SUPPRESSION_ENABLED", False)
    assert not source.contaminates(0.0, 10.0)


# --- ScheduledClicks: the baseline the other one is judged against -------


def test_scheduled_covers_a_window_around_what_it_played():
    source = ScheduledClicks(lead_s=0.01, tail_s=0.03)
    source.clicked_at(5.0)

    assert source.contaminates(5.0, 5.0)
    assert source.contaminates(4.995, 4.996)  # inside the lead
    assert source.contaminates(5.02, 5.021)  # inside the tail
    assert not source.contaminates(4.9, 4.98)
    assert not source.contaminates(5.05, 5.1)


def test_scheduled_ignores_the_microphone():
    """It already knows; observing must not change any answer it gives."""
    source = ScheduledClicks()
    source.clicked_at(1.0)
    before = source.contaminates(1.0, 1.0)
    feed(source, click_track(120.0, 2.0)[0])  # type: ignore[arg-type]
    assert source.contaminates(1.0, 1.0) == before
