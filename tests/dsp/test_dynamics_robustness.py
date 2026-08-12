"""Reading pitch across playing dynamics (pp / mf / ff), on external labels.

Every trio keeps its recorded RELATIVE level (one gain per trio), so pp is
genuinely quiet — measured -26..-41dBFS, i.e. brushing the input gate. The
claims that hold on the data:

- quiet playing is still readable: every pp clip yields readings,
- the note is right at every dynamic: within a quartertone of the label.

Cross-dynamics pitch DIFFERENCES are real instrument physics, not error —
flutes famously go flat when played softly, and these takes show it
(pp reads up to ~24 cents below ff on flute). So that spread is reported,
not asserted.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tuner.core.detector import YinDetector
from tuner.core.notes import A4_MIDI
from tuner.core.tracker import PitchTracker

DYNAMICS_DIR = Path(__file__).parent.parent / "fixtures" / "dynamics"
LABELS = DYNAMICS_DIR / "labels.json"

MAX_LABEL_DEVIATION_CENTS = 50.0


def load_trios() -> dict:
    if not LABELS.exists():
        return {}
    trios: dict = {}
    for clip, label in json.loads(LABELS.read_text()).items():
        trios.setdefault((label["instrument"], label["pitch"]), {})[label["dynamics"]] = (
            clip,
            label["midi"],
        )
    return {k: v for k, v in trios.items() if {"pp", "mf", "ff"} <= set(v)}


TRIOS = sorted(load_trios().items())

requires_dynamics = pytest.mark.skipif(
    not TRIOS, reason="dynamics fixtures absent (import_tinysol --dynamics-sets)"
)


def tracked_median_hz(path: Path) -> float | None:
    signal, sr = sf.read(path)
    detector, tracker = YinDetector(), PitchTracker()
    readings = []
    for start in range(0, len(signal) - detector.frame_size + 1, detector.hop_size):
        result = tracker.update(detector.detect(signal[start : start + detector.frame_size], sr))
        if result.freq_hz:
            readings.append(result.freq_hz)
    return float(np.median(readings)) if len(readings) >= 10 else None


@requires_dynamics
@pytest.mark.parametrize(
    "key,trio", TRIOS, ids=[f"{i.split()[0]}-{p}" for (i, p), _ in TRIOS]
)
def test_note_reads_correctly_at_every_dynamic(key, trio):
    deviations = {}
    for dynamic in ("pp", "mf", "ff"):
        clip, midi = trio[dynamic]
        detected = tracked_median_hz(DYNAMICS_DIR / clip)
        assert detected is not None, f"{clip}: unreadable at {dynamic}"
        nominal = 440.0 * 2.0 ** ((midi - A4_MIDI) / 12.0)
        deviation = 1200.0 * math.log2(detected / nominal)
        assert abs(deviation) <= MAX_LABEL_DEVIATION_CENTS, (
            f"{clip}: {deviation:+.0f} cents from the labelled note at {dynamic}"
        )
        deviations[dynamic] = deviation
    print(
        f"\n{key[0]} {key[1]}: pp {deviations['pp']:+.1f}c mf {deviations['mf']:+.1f}c "
        f"ff {deviations['ff']:+.1f}c (spread is instrument physics, not error)"
    )
