"""Offline reference pitch annotation for real audio files.

Chops audio into fixed windows and labels each with the spectral estimator
(core/spectral.py) at full precision: long analysis frames centered on each
window (non-causal — the offline advantage) and deep DTFT refinement. This is
an algorithm independent from the app's default YIN path, so comparing the
app against these annotations cross-checks two estimators rather than
validating one against itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tuner.core.spectral import estimate_f0

MIN_ANALYSIS_FRAME = 8192
MIN_LEVEL_DB = -35.0  # windows this far below the file's loud level are
# unlabeled: inter-stroke decay tails ring with sympathetic resonances
# (open strings, body modes) that are real pitch content but not what the
# player is sounding — useless as tuning ground truth


@dataclass(frozen=True)
class RefWindow:
    t0: float
    t1: float
    freq_hz: float | None
    confidence: float


def annotate(
    signal: np.ndarray,
    sr: int,
    window_s: float = 0.05,
    # 45Hz reaches the bottom of the orchestral range that this estimator
    # can actually resolve: measured on TinySOL, 58Hz lands within 6 cents
    # and 49Hz is exact. Below ~45Hz its octave decision fails (the analysis
    # bandwidth stops being narrow next to the harmonic spacing), so going
    # lower would produce confident wrong answers rather than none.
    fmin: float = 45.0,
    fmax: float = 3000.0,
    min_confidence: float = 0.5,
) -> list[RefWindow]:
    """Chop signal into window_s slices; estimate one pitch per slice.

    Each estimate uses a long analysis frame centered on the slice midpoint
    (the offline advantage: future samples are available).
    """
    hop = round(window_s * sr)
    frame_size = max(MIN_ANALYSIS_FRAME, 4 * hop)
    n_windows = len(signal) // hop
    window_rms = np.array(
        [float(np.sqrt(np.mean(signal[i * hop : (i + 1) * hop] ** 2))) for i in range(n_windows)]
    )
    loud_level = float(np.percentile(window_rms, 95)) if n_windows else 0.0
    min_rms = loud_level * 10.0 ** (MIN_LEVEL_DB / 20.0)

    windows = []
    for i in range(n_windows):
        center = i * hop + hop // 2
        lo = center - frame_size // 2
        hi = lo + frame_size
        if lo < 0 or hi > len(signal) or window_rms[i] < min_rms:
            freq, conf = None, 0.0  # edges lack context; quiet tails aren't ground truth
        else:
            freq, conf = estimate_f0(signal[lo:hi], sr, fmin, fmax, dtft_rounds=10)
            if conf < min_confidence:
                freq = None
        windows.append(
            RefWindow(t0=i * hop / sr, t1=(i + 1) * hop / sr, freq_hz=freq, confidence=conf)
        )
    return windows
