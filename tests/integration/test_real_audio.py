"""Real-audio pipeline: app output vs offline reference annotations.

Drop recordings into tests/fixtures/audio/ (wav/flac/ogg) and they are picked
up automatically. Annotations come from a sibling .ref.json if present
(generated with `python -m tuner.tools.annotate <file>`), otherwise they are
computed on the fly.

Alignment: an app reading carries the pitch around its frame CENTER even
though it is emitted at frame end, so each reading is matched to the
annotation window containing its frame center. Windows near a pitch change
(unstable neighbors) are transition regions and excluded from judgment.
"""

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tuner.analysis.reference import RefWindow, annotate
from tuner.core.notes import note_to_freq
from tuner.core.pitch import DEFAULT_FRAME_SIZE
from tuner.tools.annotate import main as annotate_cli

from tests.helpers import (
    LOW_REGISTER_HZ,
    LOW_REGISTER_TOLERANCE_CENTS,
    STABILITY_CENTS,
    TOLERANCE_CENTS,
    assert_pipeline_agreement,
    cents_error,
    compare_app_to_reference,
    load_ref_json,
)
from tests.synth import SR, add_noise, sequence, tone

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"
def test_cli_end_to_end(tmp_path):
    """Full user workflow on a known signal: wav -> CLI annotation -> comparison."""
    freqs = [note_to_freq(n, o) for n, o in [("G", 3), ("B", 3), ("D", 4), ("G", 4), ("D", 5)]]
    signal = add_noise(sequence(freqs, 0.4, instrument="violin"), 20.0, seed=5)
    wav = tmp_path / "arpeggio.wav"
    sf.write(wav, signal, SR)

    assert annotate_cli([str(wav)]) == 0
    ref_path = wav.with_suffix(".ref.json")
    window_s, ref = load_ref_json(ref_path)

    # the annotator must agree with the synthesis ground truth first
    # (stable windows only — analysis frames straddling a note boundary
    # average two pitches, and the comparison excludes those regions anyway)
    checked = 0
    for prev, cur, nxt in zip(ref, ref[1:], ref[2:]):
        trio = (prev, cur, nxt)
        if any(w.freq_hz is None for w in trio) or any(
            abs(cents_error(w.freq_hz, cur.freq_hz)) > STABILITY_CENTS for w in trio
        ):
            continue
        best = min(abs(cents_error(cur.freq_hz, f)) for f in freqs)
        assert best <= 1.5, f"annotation at {cur.t0:.2f}s off by {best:.1f} cents"
        checked += 1
    assert checked >= 20

    errors = compare_app_to_reference(signal, SR, window_s, ref)
    assert_pipeline_agreement(errors, "cli-arpeggio")


def test_annotator_vs_app_on_vibrato():
    """Harder in-process case: vibrato violin, both pipelines must agree.

    Tolerance 15 (not the usual 12): the display intentionally smooths
    (15% amplitude, ~10ms lag — docs/smoothing-tuning.md), which on a deep
    vibrato adds a few cents of instantaneous disagreement by design.
    """
    signal = tone(note_to_freq("A", 4), 2.0, instrument="violin", vibrato_cents=15.0)
    ref = annotate(signal, SR)
    errors = compare_app_to_reference(signal, SR, 0.05, ref)
    assert_pipeline_agreement(errors, "vibrato", clean_tolerance=15.0)


@pytest.mark.xfail(
    reason="known limitation: a strong non-harmonic partial (flute C6 over a "
    "G3-B3 scale) inflates the true period's CMNDF dip beyond the accept "
    "margin on ~10% of frames, so YIN drops an octave; fixing it via a larger "
    "margin breaks clean recordings (measured trade-off in pitch.py)",
    strict=False,
)
def test_limitation_nonharmonic_interference(tmp_path):
    """Built on the fly from committed sources — no binary fixture needed."""
    from tuner.tools.add_noise import main as add_noise_cli

    target = FIXTURE_DIR / "violin_scale_G3B3.aiff"
    background = FIXTURE_DIR / "flute_vib_C6.aif"
    out = tmp_path / "mix.wav"
    add_noise_cli([str(target), "--snr", "15", "--background", str(background), "-o", str(out)])

    signal, sr = sf.read(out, always_2d=True)
    window_s, ref = load_ref_json(target.with_suffix(".ref.json"))
    errors = compare_app_to_reference(signal.mean(axis=1), sr, window_s, ref)
    assert_pipeline_agreement(errors, "scale+flute (limitation)", noisy=True)


fixture_files = sorted(
    p for p in FIXTURE_DIR.glob("*")
    if p.suffix.lower() in (".wav", ".flac", ".ogg", ".aif", ".aiff")
) if FIXTURE_DIR.is_dir() else []


@pytest.mark.parametrize("audio_path", fixture_files, ids=lambda p: p.name)
def test_user_fixture(audio_path: Path):
    signal, sr = sf.read(audio_path, always_2d=True)
    mono = signal.mean(axis=1)

    ref_path = audio_path.with_suffix(".ref.json")
    if ref_path.exists():
        window_s, ref = load_ref_json(ref_path)
    else:
        window_s, ref = 0.05, annotate(mono, sr)

    errors = compare_app_to_reference(mono, sr, window_s, ref)
    labeled = [w.freq_hz for w in ref if w.freq_hz is not None]
    low_register = bool(labeled) and float(np.median(labeled)) < LOW_REGISTER_HZ
    assert_pipeline_agreement(
        errors,
        audio_path.name,
        noisy=".snr" in audio_path.name,
        clean_tolerance=LOW_REGISTER_TOLERANCE_CENTS if low_register else TOLERANCE_CENTS,
    )
