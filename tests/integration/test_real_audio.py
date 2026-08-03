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

from tests.helpers import cents_error, track_signal
from tests.synth import SR, add_noise, sequence, tone

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"
# Two independent algorithms on real audio. 12 (not 10) because they measure
# vibrato through different window lengths (46ms real-time vs 186ms centered
# annotation), which alone produces ~10c phase differences at 5-6Hz vibrato.
TOLERANCE_CENTS = 12.0
# Below ~100Hz a 46ms frame holds only a few periods and bowed bass pitch
# genuinely wobbles with bow pressure; the register is physically harder.
LOW_REGISTER_HZ = 100.0
LOW_REGISTER_TOLERANCE_CENTS = 20.0
STABILITY_CENTS = 20.0  # neighboring windows this close = stable region


def load_ref_json(path: Path) -> tuple[float, list[RefWindow]]:
    data = json.loads(path.read_text())
    windows = [RefWindow(**w) for w in data["windows"]]
    return data["window_s"], windows


def compare_app_to_reference(
    signal: np.ndarray,
    sr: int,
    window_s: float,
    ref: list[RefWindow],
) -> list[float]:
    """Returns cent errors of app readings in stable labeled regions."""

    def stable_ref_at(t: float) -> float | None:
        i = int(t / window_s)
        if not 2 <= i < len(ref) - 2:
            return None
        neighborhood = ref[i - 2 : i + 3]
        if any(w.freq_hz is None for w in neighborhood):
            return None
        if any(
            abs(cents_error(w.freq_hz, ref[i].freq_hz)) > STABILITY_CENTS
            for w in neighborhood
        ):
            return None
        return ref[i].freq_hz

    errors = []
    for t_end, freq in track_signal(signal, sr):
        truth = stable_ref_at(t_end - DEFAULT_FRAME_SIZE / sr / 2)
        if truth is None or freq is None:
            continue
        errors.append(cents_error(freq, truth))
    return errors


def assert_pipeline_agreement(
    errors: list[float],
    label: str,
    noisy: bool = False,
    clean_tolerance: float = TOLERANCE_CENTS,
) -> None:
    """Clean recordings must agree tightly, everywhere, with zero octave
    errors. Noisy fixtures are graded on being right nearly all the time
    (median, p90, bounded octave-miss rate) rather than on the worst tail —
    single-frame pitch under low-frequency-heavy noise legitimately degrades.
    """
    assert len(errors) >= 10, f"{label}: too few comparable readings ({len(errors)})"
    abs_errors = np.abs(errors)
    octave_misses = int(np.sum(abs_errors > 300))
    median = float(np.median(abs_errors))
    p90 = float(np.percentile(abs_errors, 90))
    p95 = float(np.percentile(abs_errors, 95))
    print(f"\n{label}: {len(errors)} readings, median {median:.2f}c, "
          f"p90 {p90:.2f}c, p95 {p95:.2f}c, {octave_misses} octave misses")
    if noisy:
        # p90 50 accommodates the hardest case on file: a 65Hz fundamental
        # under pink-heavy noise, where local SNR at the fundamental is near
        # 0dB and single-frame detection legitimately wobbles (the tracked
        # median stays ~1 cent)
        assert median <= 5.0
        assert p90 <= 50.0
        assert octave_misses <= 0.03 * len(errors)
    else:
        assert octave_misses == 0
        assert p95 <= clean_tolerance


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
    """Harder in-process case: vibrato violin, both pipelines must agree."""
    signal = tone(note_to_freq("A", 4), 2.0, instrument="violin", vibrato_cents=15.0)
    ref = annotate(signal, SR)
    errors = compare_app_to_reference(signal, SR, 0.05, ref)
    assert_pipeline_agreement(errors, "vibrato")


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
