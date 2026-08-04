"""Shared measurement helpers for DSP tests."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from tuner.analysis.reference import RefWindow

from tuner.core.pitch import DEFAULT_FRAME_SIZE, DEFAULT_HOP_SIZE, detect

from tests.synth import SR


def cents_error(detected_hz: float, true_hz: float) -> float:
    return 1200.0 * math.log2(detected_hz / true_hz)


def detect_frames(
    signal: np.ndarray,
    sr: int = SR,
    frame_size: int = DEFAULT_FRAME_SIZE,
    hop: int = DEFAULT_HOP_SIZE,
    min_confidence: float = 0.5,
) -> list[float]:
    """Per-frame detected frequencies, confidence-gated."""
    freqs = []
    for start in range(0, len(signal) - frame_size + 1, hop):
        result = detect(signal[start : start + frame_size], sr)
        if result.freq_hz is not None and result.confidence >= min_confidence:
            freqs.append(result.freq_hz)
    return freqs


def detect_median_hz(signal: np.ndarray, sr: int = SR, **kwargs) -> float:
    freqs = detect_frames(signal, sr, **kwargs)
    assert freqs, "no confident pitch detected in signal"
    return float(np.median(freqs))


def track_signal(
    signal: np.ndarray,
    sr: int = SR,
    detector=None,
    tracker=None,
) -> list[tuple[float, float | None]]:
    """Feed signal through the real-time pipeline exactly as the engine does.

    Returns (frame_end_time_seconds, displayed_freq) per hop — frame end is
    the moment this reading could exist in real time.
    """
    from tuner.core.detector import YinDetector
    from tuner.core.tracker import PitchTracker

    detector = detector or YinDetector()
    tracker = tracker or PitchTracker()
    frame_size = detector.frame_size
    out = []
    for start in range(0, len(signal) - frame_size + 1, detector.hop_size):
        tracked = tracker.update(detector.detect(signal[start : start + frame_size], sr))
        out.append(((start + frame_size) / sr, tracked.freq_hz))
    return out


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
