"""Metronome timing, checked against arithmetic.

This is one of the few areas where ground truth needs no annotator and no
recording: beat k is at k * 60/BPM seconds, full stop. So these tests do not
compare the metronome against another implementation of itself — they compare
the *rendered audio* against the closed-form answer, which is the outside
authority docs/process/ground-truth.md asks for.
"""

import math

import numpy as np
import pytest
from numpy.lib.stride_tricks import sliding_window_view

from tests.metrics import record
from tuner.core.metronome import (
    CLICK_AMPLITUDE,
    CLICK_SOUNDS,
    DEFAULT_SOUND,
    MAX_BPM,
    MIN_BPM,
    ClickSchedule,
    Metronome,
    click_waveform,
    sound_waveform,
)

SR = 44100


def onset_samples(signal: np.ndarray, threshold: float = 0.05) -> list[int]:
    """Where clicks actually are in the rendered audio.

    Independent of the scheduler on purpose: if the renderer put a click
    somewhere the schedule did not, this finds it.

    Measured on a backward-looking envelope rather than on |signal| directly.
    The click is a 1000Hz tone, so its own waveform returns to zero 2000 times
    a second and a bare threshold reports every half-cycle as a new onset
    (2340 "clicks" in a 60-click signal, which is how this was found).
    """
    window = round(0.002 * SR)  # 2ms, longer than one cycle of the click tone
    padded = np.concatenate([np.zeros(window - 1), np.abs(signal)])
    envelope = sliding_window_view(padded, window).max(axis=1)
    loud = envelope > threshold
    return list(np.flatnonzero(loud & ~np.concatenate([[False], loud[:-1]])))


# --- the schedule itself -------------------------------------------------


@pytest.mark.parametrize("bpm", [30.0, 60.0, 100.0, 120.0, 137.0, 208.0, 300.0])
def test_beat_lands_where_arithmetic_says(bpm):
    schedule = ClickSchedule(bpm, SR)
    period = 60.0 * SR / bpm
    worst = max(abs(schedule.beat_sample(k) - k * period) for k in range(200))
    assert worst <= 0.5, f"{bpm} BPM: off by {worst} samples"


@pytest.mark.parametrize("bpm", [60.0, 137.0, 208.0])
def test_no_drift_over_ten_minutes(bpm):
    """The failure this rules out is invisible for a minute and obvious after
    ten, which is exactly how long someone practises with a metronome."""
    schedule = ClickSchedule(bpm, SR)
    beats = int(10 * 60 * bpm / 60)
    period = 60.0 * SR / bpm
    error_s = abs(schedule.beat_sample(beats) - beats * period) / SR
    record(f"metronome/drift_10min_{bpm:g}bpm/ms", error_s * 1000.0, unit="ms")
    assert error_s * 1000.0 <= 0.05


def test_tempo_change_keeps_the_beat_in_progress():
    """Turning the dial must not shove the current beat sideways: the beat
    already sounding keeps its sample, and only the next interval changes."""
    schedule = ClickSchedule(120.0, SR)
    last = schedule.beat_sample(10)
    midway = last + SR // 8  # a moment after that beat sounded
    schedule.rebase(90.0, midway)
    assert schedule.beat_sample(0) == last
    assert schedule.beat_sample(1) - last == pytest.approx(60.0 * SR / 90.0, abs=1)


def test_bpm_is_clamped_not_trusted():
    assert ClickSchedule(0.0, SR).bpm == MIN_BPM
    assert ClickSchedule(10_000.0, SR).bpm == MAX_BPM


# --- the rendered audio --------------------------------------------------


@pytest.mark.parametrize("blocks", [(64,), (256,), (1024,), (37, 512, 91, 300)])
def test_block_size_invariance(blocks):
    """The device picks the block size; the audio must not depend on it."""
    reference = Metronome(SR, 120.0).render(SR * 3)

    metronome, chunks, produced = Metronome(SR, 120.0), [], 0
    i = 0
    while produced < SR * 3:
        frames = min(blocks[i % len(blocks)], SR * 3 - produced)
        chunks.append(metronome.render(frames))
        produced += frames
        i += 1
    assert np.array_equal(np.concatenate(chunks), reference)


@pytest.mark.parametrize("bpm", [40.0, 120.0, 208.0])
def test_rendered_clicks_are_where_they_should_be(bpm):
    """Measured off the waveform, not asked of the scheduler."""
    seconds = 30
    signal = Metronome(SR, bpm).render(SR * seconds)
    onsets = onset_samples(signal)

    assert len(onsets) == math.ceil(seconds * bpm / 60), "wrong number of clicks"
    intervals = np.diff(onsets) / SR * 1000.0
    expected_ms = 60_000.0 / bpm
    worst = float(np.max(np.abs(intervals - expected_ms)))
    record(f"metronome/interval_error_{bpm:g}bpm/ms", worst, unit="ms")
    # one sample is 0.023ms; the bound is what rounding to whole samples can
    # cost on either side of an interval, and nothing else may creep in
    assert worst <= 2000.0 / SR, f"{bpm} BPM: worst interval error {worst:.4f}ms"


def test_click_starts_and_ends_at_silence():
    """A click mixed into a stream must not add a step of its own."""
    click = click_waveform(SR)
    assert click[0] == 0.0
    assert abs(click[-1]) < 1e-9
    assert np.max(np.abs(click)) <= 1.0


def test_click_crossing_a_block_boundary_is_not_cut():
    """Blocks land wherever they land; a click straddling two of them is the
    normal case. Rendering it in halves must equal rendering it whole."""
    span = len(click_waveform(SR))
    schedule = ClickSchedule(120.0, SR)
    beat = schedule.beat_sample(1)
    split = beat + span // 2

    whole = Metronome(SR, 120.0).render(split + span)
    piecewise = Metronome(SR, 120.0)
    halves = np.concatenate([piecewise.render(split), piecewise.render(span)])
    assert np.array_equal(halves, whole)


def test_reset_starts_a_fresh_bar_not_a_resume():
    metronome = Metronome(SR, 120.0)
    metronome.render(SR // 3)
    metronome.reset()
    assert metronome.position == 0
    # 1, not 0: the click starts at zero amplitude and takes a sample or two
    # to cross the detector's threshold. Constant across clicks, so it cancels
    # out of every interval measured above.
    assert onset_samples(metronome.render(SR))[0] <= 2


def test_beat_samples_in_reports_the_span_it_was_asked_about():
    """What the click suppressor will ask (app/engine.py): which beats sound
    during this stretch of audio."""
    metronome = Metronome(SR, 120.0)
    period = SR // 2
    assert metronome.beat_samples_in(0, SR) == [0, period]
    assert metronome.beat_samples_in(1, period - 1) == []  # between two beats
    assert metronome.beat_samples_in(period, 1) == [period]  # exactly on one


# --- the sounds a beat can make ------------------------------------------


@pytest.mark.parametrize("name", list(CLICK_SOUNDS))
def test_every_sound_is_mixable(name):
    """Whatever it sounds like, it is mixed into a stream at an arbitrary
    sample: it has to start and end at silence, or it adds a step of its own
    that the tuner then hears as a transient the metronome never played."""
    wave = sound_waveform(name, SR)
    assert wave[0] == 0.0
    assert abs(wave[-1]) < 1e-9
    # at the asked-for amplitude and never above it. Not exactly equal: the
    # original click is written analytically rather than normalised, so its
    # peak lands wherever the sine's crest falls between two samples (0.4969).
    peak = float(np.max(np.abs(wave)))
    assert 0.95 * CLICK_AMPLITUDE <= peak <= CLICK_AMPLITUDE
    # short enough not to smear the beat it marks, even at 300 BPM (200ms)
    assert 0.0 < len(wave) / SR <= 0.060


@pytest.mark.parametrize("name", list(CLICK_SOUNDS))
def test_every_sound_is_deterministic(name):
    """Two of these are built from noise. A metronome that renders differently
    run to run could not be checked to the sample anywhere in this file."""
    assert np.array_equal(sound_waveform(name, SR), sound_waveform(name, SR))


def test_the_default_sound_is_the_one_that_was_the_only_one():
    assert DEFAULT_SOUND == "클릭"
    assert np.array_equal(sound_waveform(DEFAULT_SOUND, SR), click_waveform(SR))


def test_an_unknown_sound_falls_back_rather_than_going_silent():
    """A settings file naming a sound this build dropped must not leave the
    metronome playing nothing."""
    assert np.array_equal(sound_waveform("존재하지 않음", SR), sound_waveform(DEFAULT_SOUND, SR))


def test_sounds_are_actually_different():
    """Guards against a registry where two entries quietly resolve the same."""
    rendered = [sound_waveform(name, SR) for name in CLICK_SOUNDS]
    for i, a in enumerate(rendered):
        for b in rendered[i + 1 :]:
            assert len(a) != len(b) or not np.array_equal(a, b)


def test_changing_sound_keeps_the_beat():
    """Swapping the waveform must not move where the beats are."""
    metronome = Metronome(SR, 120.0)
    before = metronome.beat_samples_in(0, SR)
    metronome.set_sound("틱")
    assert metronome.sound == "틱"
    assert metronome.beat_samples_in(0, SR) == before
    assert onset_samples(metronome.render(SR))[0] <= 2
