"""Ground-truth validation against sources outside this codebase.

Our reference annotations are produced by our own estimator, so on real
recordings "app agrees with annotator" could in principle mean "both wrong
the same way" — the more so since the app's weak-fundamental check and the
annotator share `divide_to_true_f0`. Two independent authorities keep that
honest:

1. **The recordings' own labels.** The Iowa MIS files are named by the note
   played (violin/A4.flac, cello_arco_C2.aif). That label was assigned by
   the people who made the recordings — it is external to us, and it pins
   the octave and note name absolutely.
2. **An independent implementation.** librosa's pyin is a different
   codebase by different authors. Where it disagrees with us, authority 1
   decides who is right (measured: it is us — pyin takes the oboe's
   dominant harmonic on the two clips where the fundamental is very weak).
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tuner.core.notes import NOTE_NAMES, note_to_freq

BANK_DIR = Path(__file__).parent.parent / "fixtures" / "notes"
AUDIO_DIR = Path(__file__).parent.parent / "fixtures" / "audio"

FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}

# Instruments tune to their own reference and players are human: a played
# note lands within a quartertone of nominal, never near the next semitone.
MAX_LABEL_DEVIATION_CENTS = 50.0
MAX_PYIN_DISAGREEMENT_CENTS = 12.0

requires_bank = pytest.mark.skipif(
    not (BANK_DIR / "bank.json").exists(), reason="note bank not built"
)


def label_to_freq(label: str, a4_hz: float = 440.0) -> float:
    name, octave = label[:-1], int(label[-1])
    return note_to_freq(FLAT_TO_SHARP.get(name, name), octave, a4_hz=a4_hz)


def cents(a: float, b: float) -> float:
    return 1200.0 * math.log2(a / b)


def pyin_median_hz(signal: np.ndarray, sr: int) -> float | None:
    librosa = pytest.importorskip("librosa", reason="pip install -e '.[crosscheck]'")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        f0, _voiced, _prob = librosa.pyin(
            signal.astype(np.float32), sr=sr, fmin=55, fmax=3200, frame_length=4096
        )
    voiced = f0[~np.isnan(f0)]
    return float(np.median(voiced)) if len(voiced) >= 5 else None


def bank_entries() -> list[tuple[str, str, dict]]:
    manifest = json.loads((BANK_DIR / "bank.json").read_text())
    return [(i, n, info) for i in sorted(manifest) for n, info in sorted(manifest[i].items())]


# single-note fixtures whose filename states the note (external label)
LABELED_AUDIO = [
    (p, p.stem.split(".")[0].rsplit("_", 1)[-1])
    for p in sorted(AUDIO_DIR.glob("*"))
    if p.suffix.lower() in (".aif", ".aiff", ".wav", ".flac")
    and "scale" not in p.stem
    and p.stem.split(".")[0].rsplit("_", 1)[-1][:-1].rstrip("b#") in NOTE_NAMES + tuple(FLAT_TO_SHARP)
] if AUDIO_DIR.is_dir() else []


@requires_bank
def test_bank_matches_recording_labels():
    """Every bank clip's annotated pitch must be the note the file says it
    is — an octave error cannot hide behind our own estimator."""
    worst = 0.0
    for instrument, note, info in bank_entries():
        nominal = label_to_freq(note)
        deviation = cents(info["freq_hz"], nominal)
        assert abs(deviation) <= MAX_LABEL_DEVIATION_CENTS, (
            f"{instrument}/{note}: annotated {info['freq_hz']:.1f}Hz is "
            f"{deviation:+.0f} cents from {note} ({nominal:.1f}Hz)"
        )
        worst = max(worst, abs(deviation))
    print(f"\nbank vs filename labels: {len(bank_entries())} clips, worst {worst:.0f} cents")


@pytest.mark.parametrize("path,label", LABELED_AUDIO, ids=lambda v: getattr(v, "stem", v))
def test_fixture_matches_recording_label(path: Path, label: str):
    """The committed .ref.json — what every real-audio test grades against —
    must name the note the recording says it is."""
    ref_path = path.with_suffix(".ref.json")
    if ref_path.exists():
        windows = [w["freq_hz"] for w in json.loads(ref_path.read_text())["windows"] if w["freq_hz"]]
    else:
        from tuner.analysis.reference import annotate

        signal, sr = sf.read(path, always_2d=True)
        windows = [w.freq_hz for w in annotate(signal.mean(axis=1), sr) if w.freq_hz]
    assert windows, f"{path.name}: nothing annotated"
    deviation = cents(float(np.median(windows)), label_to_freq(label))
    assert abs(deviation) <= MAX_LABEL_DEVIATION_CENTS, (
        f"{path.name}: annotated {deviation:+.0f} cents from labelled {label}"
    )


@requires_bank
@pytest.mark.parametrize(
    "instrument,note", [(i, n) for i, n, _ in bank_entries()][::12]  # sampled; full run below
)
def test_bank_agrees_with_independent_pyin(instrument, note):
    """Sampled cross-check against librosa's pyin — a different algorithm by
    different authors. Disagreement is allowed only where the recording's own
    label backs us (see the oboe cases in the module docstring)."""
    info = json.loads((BANK_DIR / "bank.json").read_text())[instrument][note]
    signal, sr = sf.read(BANK_DIR / instrument / f"{note}.flac")
    external = pyin_median_hz(signal, sr)
    if external is None:
        pytest.skip("pyin found no voiced frames")

    disagreement = cents(external, info["freq_hz"])
    if abs(disagreement) <= MAX_PYIN_DISAGREEMENT_CENTS:
        return
    ours_vs_label = cents(info["freq_hz"], label_to_freq(note))
    assert abs(ours_vs_label) <= MAX_LABEL_DEVIATION_CENTS, (
        f"{instrument}/{note}: we say {info['freq_hz']:.1f}Hz, pyin says "
        f"{external:.1f}Hz ({disagreement:+.0f}c), and the label does not back us"
    )
    print(f"\n{instrument}/{note}: pyin off by {disagreement:+.0f}c; label backs us")


@pytest.mark.crosscheck
@requires_bank
def test_whole_bank_against_pyin():
    """Full sweep (slow — opt in with `pytest -m crosscheck`)."""
    disagreements = []
    for instrument, note, info in bank_entries():
        signal, sr = sf.read(BANK_DIR / instrument / f"{note}.flac")
        external = pyin_median_hz(signal, sr)
        if external is None:
            continue
        d = cents(external, info["freq_hz"])
        if abs(d) > MAX_PYIN_DISAGREEMENT_CENTS:
            label_dev = cents(info["freq_hz"], label_to_freq(note))
            disagreements.append((f"{instrument}/{note}", d, label_dev))
    for name, d, label_dev in disagreements:
        print(f"\n{name}: pyin {d:+.0f}c away; ours is {label_dev:+.0f}c from its label")
        assert abs(label_dev) <= MAX_LABEL_DEVIATION_CENTS, f"{name}: label does not back us"
    print(f"\n{len(disagreements)} disagreements out of {len(bank_entries())} clips")
