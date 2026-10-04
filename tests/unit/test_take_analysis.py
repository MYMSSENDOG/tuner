"""A take's intonation: the readings the meter would have shown."""

import math

import numpy as np
import pytest

from tests.synth import SR, tone
from tuner.app import recorder
from tuner.app.take_analysis import analyse


def median_cents(track):
    values = [c for c in track.cents if not math.isnan(c)]
    return float(np.median(values))


def test_a_sharp_note_reads_sharp():
    played = tone(440.0 * 2 ** (12 / 1200), 1.5, instrument="violin")
    track = analyse(played, SR, a4_hz=440.0)
    assert track.complete
    assert median_cents(track) == pytest.approx(12.0, abs=1.0)
    assert {label for label in track.labels if label} == {"A4"}


def test_the_take_is_judged_against_its_own_reference():
    """The same sound reads differently against a different A4 - which is why
    the window passes the A4 stored in the take, not the current setting."""
    played = tone(440.0 * 2 ** (12 / 1200), 1.5, instrument="violin")
    at_442 = analyse(played, SR, a4_hz=442.0)
    expected = 12.0 - 1200 * math.log2(442.0 / 440.0)
    assert median_cents(at_442) == pytest.approx(expected, abs=1.0)


def test_the_reference_is_read_back_from_the_take(tmp_path):
    path = recorder.save_take(tone(440.0, 0.5), SR, "기준", directory=tmp_path, a4_hz=442.0)
    assert recorder.take_a4(path) == pytest.approx(442.0)


def test_progress_grows_and_stopping_gives_up():
    played = tone(440.0, 3.0, instrument="violin")
    seen = []
    analyse(played, SR, 440.0, on_progress=lambda t: seen.append(t.analysed_s))
    assert seen == sorted(seen) and len(seen) >= 2
    assert analyse(played, SR, 440.0, should_stop=lambda: True) is None
