"""Validation against a third party's labelled recordings (TinySOL).

Everything else in the suite is graded against pitches this codebase
computed. Here the labels come from the TinySOL authors (IRCAM), on
instruments and playing styles we did not choose: if our annotator drifts or
picks the wrong octave on an instrument we never tested, this fails.

Fixtures live in tests/fixtures/external/ and are optional — the tests skip
when the directory is absent. Import a subset with
`python -m tuner.tools.import_tinysol <extracted> <metadata.csv>
tests/fixtures/external`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tuner.analysis.reference import annotate
from tuner.core.detector import YinDetector
from tuner.core.notes import NOTE_NAMES, A4_MIDI
from tuner.core.tracker import PitchTracker

EXTERNAL_DIR = Path(__file__).parent.parent / "fixtures" / "external"
LABELS = EXTERNAL_DIR / "labels.json"

# Instruments are tuned by their players; a quartertone bounds "the note the
# label says" without admitting a neighbouring semitone.
MAX_LABEL_DEVIATION_CENTS = 50.0


def midi_to_hz(midi: int, a4_hz: float = 440.0) -> float:
    return a4_hz * 2.0 ** ((midi - A4_MIDI) / 12.0)


def load_labels() -> dict[str, dict]:
    return json.loads(LABELS.read_text()) if LABELS.exists() else {}


CLIPS = sorted(load_labels().items())

requires_external = pytest.mark.skipif(
    not CLIPS, reason="external fixtures absent (see module docstring)"
)


def median_detected_hz(signal: np.ndarray, sr: int) -> float | None:
    """What the real-time path would settle on for this clip."""
    detector, tracker = YinDetector(), PitchTracker()
    readings = []
    for start in range(0, len(signal) - detector.frame_size + 1, detector.hop_size):
        tracked = tracker.update(detector.detect(signal[start : start + detector.frame_size], sr))
        if tracked.freq_hz:
            readings.append(tracked.freq_hz)
    return float(np.median(readings)) if len(readings) >= 10 else None


@requires_external
@pytest.mark.parametrize("clip,label", CLIPS, ids=[c for c, _ in CLIPS])
def test_app_matches_external_label(clip, label):
    signal, sr = sf.read(EXTERNAL_DIR / clip)
    detected = median_detected_hz(np.atleast_1d(signal), sr)
    assert detected is not None, f"{clip}: no stable reading"
    deviation = 1200 * math.log2(detected / midi_to_hz(label["midi"]))
    assert abs(deviation) <= MAX_LABEL_DEVIATION_CENTS, (
        f"{clip} ({label['instrument']} {label['pitch']}): app read "
        f"{detected:.1f}Hz, {deviation:+.0f} cents from the labelled pitch"
    )


# The annotator's search starts at 60Hz, so notes in octave 1 (E1 = 41Hz)
# are out of its range: it either finds nothing or locks an octave up. They
# are listed one by one rather than filtered by frequency, so that a new
# failure — or a fix — shows up instead of widening quietly.
#
# The real-time path does handle these (its second, longer window reaches
# down to 38Hz), and test_app_matches_external_label above covers them: it
# grades against the dataset's labels, needing no annotation from us.
ANNOTATOR_BELOW_RANGE = {
    "Acc-E1.flac",
    "Bn-As1.flac",
    "Cb-E1.flac",
    "Hn-G1.flac",
}


@pytest.mark.crosscheck  # ~2min: annotates every clip. Opt in with -m crosscheck
@requires_external
def test_annotator_matches_external_labels_and_reveals_dataset_tuning():
    """The annotator must land on the labelled note for every clip. The
    signed deviations also expose the dataset's tuning reference: a
    consistent offset means the ensemble tuned to something other than
    A4=440, which is information, not error."""
    deviations = []
    for clip, label in CLIPS:
        signal, sr = sf.read(EXTERNAL_DIR / clip)
        windows = [w.freq_hz for w in annotate(np.atleast_1d(signal), sr) if w.freq_hz]
        below_range = clip in ANNOTATOR_BELOW_RANGE
        if not windows:
            assert below_range, f"{clip}: nothing annotated"
            continue
        deviation = 1200 * math.log2(float(np.median(windows)) / midi_to_hz(label["midi"]))
        if below_range:
            assert abs(deviation) > MAX_LABEL_DEVIATION_CENTS, (
                f"{clip} now annotates correctly — drop it from ANNOTATOR_BELOW_RANGE"
            )
            continue
        assert abs(deviation) <= MAX_LABEL_DEVIATION_CENTS, (
            f"{clip} ({label['instrument']} {label['pitch']}): {deviation:+.0f} cents off"
        )
        deviations.append(deviation)

    implied_a4 = 440.0 * 2 ** (float(np.median(deviations)) / 1200)
    print(
        f"\n{len(deviations)} external clips: median {np.median(deviations):+.1f}c, "
        f"spread {np.percentile(deviations, 5):+.0f}..{np.percentile(deviations, 95):+.0f}c "
        f"(implies A4 = {implied_a4:.1f}Hz)"
    )
