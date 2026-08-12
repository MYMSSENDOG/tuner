"""YIN pitch detection (CMNDF + parabolic interpolation).

Pure function: one frame of samples in, one PitchResult out. No I/O, no state.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tuner.core import SILENCE_RMS

DEFAULT_FRAME_SIZE = 2048
DEFAULT_HOP_SIZE = 256


@dataclass(frozen=True)
class PitchResult:
    freq_hz: float | None
    confidence: float  # 1 - CMNDF minimum; higher = more periodic


def _parabolic_min(d: np.ndarray, i: int) -> float:
    if i <= 0 or i + 1 >= len(d):
        return float(i)
    a, b, c = d[i - 1], d[i], d[i + 1]
    denom = a - 2 * b + c
    if denom <= 0:
        return float(i)
    return i + 0.5 * (a - c) / denom


def _difference_function(x: np.ndarray, w: int) -> np.ndarray:
    """d[tau] = sum_{j<w} (x[j] - x[j+tau])^2 for tau in [0, w), via FFT."""
    n = x.size
    energy = np.concatenate(([0.0], np.cumsum(x * x)))
    nfft = 1 << (2 * n - 1).bit_length()
    fx = np.fft.rfft(x, nfft)
    fw = np.fft.rfft(x[:w], nfft)
    cross = np.fft.irfft(fx * np.conj(fw), nfft)[:w]
    d = energy[w] + (energy[w : 2 * w] - energy[:w]) - 2 * cross
    return np.maximum(d, 0.0)


def detect(
    frame: np.ndarray,
    sr: int,
    fmin: float = 60.0,
    fmax: float = 3000.0,
    threshold: float = 0.12,
) -> PitchResult:
    x = np.asarray(frame, dtype=np.float64)
    w = x.size // 2
    if np.sqrt(np.mean(x * x)) < SILENCE_RMS:
        return PitchResult(None, 0.0)

    d = _difference_function(x, w)

    # cumulative mean normalized difference
    taus = np.arange(1, w)
    cmndf = np.empty(w)
    cmndf[0] = 1.0
    cmndf[1:] = d[1:] * taus / np.maximum(np.cumsum(d[1:]), 1e-12)

    tau_lo = max(2, int(sr / fmax))
    tau_hi = min(w - 1, int(sr / fmin))
    if tau_lo >= tau_hi:
        return PitchResult(None, 0.0)

    i = tau_lo + int(np.argmin(cmndf[tau_lo:tau_hi]))
    i = _prefer_smallest_period(cmndf, i, tau_lo, tau_hi, threshold)

    confidence = float(1.0 - cmndf[i])
    tau = _parabolic_min(d, i)
    if tau <= 0:
        return PitchResult(None, 0.0)

    tau = _refine_at_higher_lag(d, tau, w)
    return PitchResult(freq_hz=sr / tau, confidence=confidence)


def _prefer_smallest_period(
    cmndf: np.ndarray, i: int, tau_lo: int, tau_hi: int, threshold: float
) -> int:
    """Resolve subharmonic errors: any multiple of the true period is also a
    period, and CMND normalization makes dips at larger lags spuriously deep
    under noise. Among dips comparably deep to the global minimum, the one at
    the smallest lag (checked at each integer divisor of the minimum's lag)
    is the true period.

    Dip depths are compared at their parabola-interpolated minima, not at
    sampled lags: a non-integer true period pays a quantization penalty at
    lag 1*T that near-integer higher multiples don't, which would make any
    sampled-value comparison systematically unfair to the true period.
    """
    # Margin trade-off, measured on the fixture suite: larger admits true
    # periods whose dip an interfering partial inflated (octave-down fix),
    # but starts accepting false small-lag dips on clean note transitions.
    # 0.08 keeps clean recordings error-free; strong non-harmonic
    # interference (see the xfail in test_real_audio) is a known limitation.
    accept = max(threshold, _interpolated_dip(cmndf, i) + 0.08)
    for k in range(int(i / tau_lo), 1, -1):  # smallest candidate lag first
        center = i / k
        lo = max(tau_lo, int(center) - 2)
        hi = min(tau_hi, int(center) + 3)
        j = lo + int(np.argmin(cmndf[lo:hi]))
        # the window is centered on an estimate; descend to the actual local min
        while j + 1 < tau_hi and cmndf[j + 1] < cmndf[j]:
            j += 1
        while j - 1 >= tau_lo and cmndf[j - 1] < cmndf[j]:
            j -= 1
        if _interpolated_dip(cmndf, j) < accept and _is_true_period(
            cmndf, j, k, tau_hi, accept
        ):
            return j
    return i


def _is_true_period(cmndf: np.ndarray, j: int, k: int, tau_hi: int, accept: float) -> bool:
    """A real period dips at EVERY multiple of its lag. A dip at lag j that is
    an artifact of period k*j (e.g. the half-period dip a strong interfering
    partial carves out) has no dip at multiples of j that aren't multiples of
    k*j — so probe the smallest such multiple: 3j when k is even, 2j when odd.
    """
    # probe 2j, except for k == 2 where 2j == k*j is the global minimum
    # itself and proves nothing (for any other k, 2j is not a multiple of k*j)
    m = 3 if k == 2 else 2
    probe = m * j
    if probe >= tau_hi:
        return True  # out of range, nothing to disprove the candidate with
    lo, hi = probe - 2, probe + 3
    p = lo + int(np.argmin(cmndf[lo:hi]))
    while p + 1 < tau_hi and cmndf[p + 1] < cmndf[p]:
        p += 1
    while p - 1 > 0 and cmndf[p - 1] < cmndf[p]:
        p -= 1
    return _interpolated_dip(cmndf, p) < accept


def _interpolated_dip(cmndf: np.ndarray, i: int) -> float:
    """Depth of the local minimum around i, parabola-interpolated."""
    if i <= 0 or i + 1 >= len(cmndf):
        return float(cmndf[i])
    a, b, c = float(cmndf[i - 1]), float(cmndf[i]), float(cmndf[i + 1])
    denom = a - 2 * b + c
    if denom <= 0:
        return b
    return max(0.0, b - (a - c) ** 2 / (8 * denom))


def _refine_at_higher_lag(d: np.ndarray, tau: float, w: int) -> float:
    """Sharpen a short-lag estimate using the difference-function minimum at m*tau.

    The lag axis quantizes frequency, so for high pitches (small tau) even
    parabolic interpolation is too coarse for cent accuracy. Periodicity puts
    equivalent minima at every multiple of tau; interpolating at m*tau divides
    the interpolation error by m.
    """
    m = int((w - 2) / tau)
    if m < 2:
        return tau
    center = round(m * tau)
    radius = max(2, int(0.45 * tau))
    lo = max(1, center - radius)
    hi = min(w - 1, center + radius + 1)
    j = lo + int(np.argmin(d[lo:hi]))
    refined = _parabolic_min(d, j) / m
    # reject if the high-lag minimum disagrees with the short-lag estimate
    # (can happen when noise corrupts the far minima)
    if abs(refined - tau) > 0.5:
        return tau
    return refined
