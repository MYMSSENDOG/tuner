"""The tuner must not try to tune its own metronome.

The microphone hears the click and the detector has no idea it is not the
instrument. Measured on real recordings with clicks mixed over them, the
display picks up names that were never played — `B5` is the 1kHz click
itself, and it arrives once a beat.

Two sources answer "was that us?", and both are exercised here because the
second is only believable against the first:

- `ScheduledClicks` knows because we played it. Perfect information, and it
  needs the output device's latency to be right.
- `HeardClicks` is told the tempo and finds the phase in the microphone. That
  is what the app runs. It costs two beats to lock, and it buys back the case
  the scheduled one gets wrong: on headphones no click ever arrives, and it
  freezes nothing rather than freezing 8-29% of frames for a sound that was
  never in the room.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tests.fakes import FakeAudioInput
from tests.metrics import record
from tests.synth import SR, sequence
from tuner.app.engine import TunerEngine
from tuner.core.interference import HeardClicks, ScheduledClicks
from tuner.core.metronome import click_waveform

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"
BLOCK = 256
# The range a metronome is actually set to: 40 is a slow Largo, 200 is past
# Presto. Outside it is the clamp's business (tests/unit/test_metronome.py).
TEMPI = (40.0, 60.0, 100.0, 144.0, 200.0)


def mix_clicks(base: np.ndarray, sr: int, bpm: float, level: float = 0.5):
    """`base` with metronome clicks over it, and when each one was audible."""
    out = base.copy()
    click = click_waveform(sr) * level / 0.5
    period = 60.0 * sr / bpm
    times, k = [], 0
    while round(k * period) + len(click) < len(out):
        at = round(k * period)
        out[at : at + len(click)] += click
        times.append(at / sr)
        k += 1
    return out, times


def load_fixture(name: str, seconds: float = 6.0):
    signal, sr = sf.read(str(FIXTURE_DIR / name), always_2d=True)
    return np.ascontiguousarray(signal.mean(axis=1))[: int(seconds * sr)], sr


class Counting:
    """Any interference source, plus how often it froze the display."""

    def __init__(self, inner):
        self.inner = inner
        self.frozen = 0
        self.asked = 0

    def observe(self, block, t_end, sr):
        self.inner.observe(block, t_end, sr)

    def contaminates(self, t_start, t_end):
        self.asked += 1
        hit = self.inner.contaminates(t_start, t_end)
        self.frozen += hit
        return hit

    @property
    def frozen_fraction(self) -> float:
        return self.frozen / max(self.asked, 1)


def run(signal: np.ndarray, sr: int = SR, interference=None) -> list:
    """The real engine over the signal, with a clock tied to the audio.

    The fake clock is the point: suppression compares wall-clock spans, so a
    test that let real time pass would be measuring the machine's mood.
    """
    readings: list = []
    now = [0.0]
    fake = FakeAudioInput(signal, block_size=BLOCK, sr=sr)
    engine = TunerEngine(
        fake, readings.append, interference=interference, clock=lambda: now[0]
    )
    engine.start()
    fake.pump(before_block=lambda start: now.__setitem__(0, (start + BLOCK) / sr))
    engine.stop()
    return readings


def name_segments(readings: list) -> list[str]:
    out: list[str] = []
    for reading in readings:
        label = reading.note.label if reading.note else None
        if label and (not out or out[-1] != label):
            out.append(label)
    return out


def heard(bpm: float) -> Counting:
    source = HeardClicks()
    source.set_period(60.0 / bpm)  # the tempo is the whole of the prior
    return Counting(source)


def scheduled(times: list[float]) -> Counting:
    source = ScheduledClicks()
    for t in times:
        source.clicked_at(t)
    return Counting(source)


# --- synthetic, where the answer is known exactly ------------------------


@pytest.fixture
def played():
    """Two violin A4s with a rest between them — a bar someone counts in."""
    return sequence([440.0, 440.0], note_duration=1.0, gap=1.0, instrument="violin")


@pytest.mark.parametrize("bpm", [120.0, 200.0])
def test_clicks_do_not_reach_the_display(played, bpm):
    signal, times = mix_clicks(played, SR, bpm)
    source = scheduled(times)
    segments = name_segments(run(signal, SR, source))

    assert set(segments) <= {"A4", "G7"}, f"metronome reached the meter: {segments}"
    # 2 for the notes, +1 for the pre-existing release artefact (below)
    assert len(segments) <= 3
    record(f"metronome/frozen_fraction_{bpm:g}bpm", source.frozen_fraction * 100, unit="%")
    assert source.frozen_fraction <= 0.35


@pytest.mark.parametrize("bpm", [120.0, 200.0])
def test_suppression_is_actually_doing_something(played, bpm):
    """Power check (docs/process/regression.md): with the clicks unannounced,
    the same audio must visibly break the display. A guard that cannot be
    made to fail is not guarding anything."""
    signal, _ = mix_clicks(played, SR, bpm)
    segments = name_segments(run(signal, SR, interference=None))
    assert [s for s in segments if s not in ("A4", "G7")], "clicks did not disturb it"
    assert len(segments) >= 6  # measured 7 at 120 BPM, 17 at 200, against 2-3 guarded


def test_the_stray_g7_is_not_the_metronome(played):
    """Guards a claim the other tests lean on: the one name suppression is
    allowed to leave behind is there with no metronome at all — it is in the
    decay after a note stops, review-followups item 10's family."""
    assert "G7" in name_segments(run(played, SR, interference=None))


def test_silence_between_clicks_is_still_silence():
    """Suppression must not manufacture a reading: with nothing but clicks,
    nothing is displayed at all."""
    signal, times = mix_clicks(np.zeros(int(5 * SR)), SR, 120.0)
    assert name_segments(run(signal, SR, scheduled(times))) == []


# --- real instruments, across the tempi a metronome is set to -------------

FIXTURES = ("cello_arco_A3.aif", "oboe_scale_C4B4.aiff", "trumpet_novib_G3.aif")


@pytest.mark.parametrize("fixture", FIXTURES)
@pytest.mark.parametrize("bpm", TEMPI)
def test_real_instrument_under_a_metronome(fixture, bpm):
    """The whole point, on real audio: play through a metronome at 40-200 BPM
    and the meter must show what was played, not what was ticking."""
    base, sr = load_fixture(fixture)
    clean = name_segments(run(base, sr))
    signal, _ = mix_clicks(base, sr, bpm)

    unguarded = name_segments(run(signal, sr))
    source = heard(bpm)
    guarded = name_segments(run(signal, sr, source))

    stray = [s for s in guarded if s not in clean]
    record(f"metronome/stray_{fixture}_{bpm:g}bpm", len(stray), unit="names")
    record(
        f"metronome/frozen_{fixture}_{bpm:g}bpm", source.frozen_fraction * 100, unit="%"
    )
    # The lock costs the first two beats, so a click or two can still reach
    # the display before it takes hold. What may not happen is the metronome
    # owning the meter.
    assert len(stray) <= 3, f"{fixture} @ {bpm}: {guarded} vs clean {clean}"
    assert len(guarded) <= len(unguarded)
    assert source.frozen_fraction <= 0.35


@pytest.mark.parametrize("bpm", [60.0, 200.0])
def test_headphones_cost_nothing(bpm):
    """The case the scheduled source gets wrong. The metronome is running but
    the player wears headphones, so no click reaches the microphone: listening
    for it freezes nothing, while trusting the schedule freezes frames for a
    sound that was never in the room."""
    base, sr = load_fixture("cello_arco_A3.aif")

    listening = heard(bpm)
    run(base, sr, listening)
    assert listening.frozen == 0
    assert not listening.inner.locked

    period = 60.0 / bpm
    trusting = scheduled([k * period for k in range(int(len(base) / sr / period) + 1)])
    run(base, sr, trusting)
    record(f"metronome/headphone_waste_{bpm:g}bpm", trusting.frozen_fraction * 100, unit="%")
    assert trusting.frozen_fraction > 0.05  # the thing being avoided
