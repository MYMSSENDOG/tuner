"""Spectral f0 estimation: HPS + per-harmonic continuous DTFT refinement.

The second, independent pitch estimator (the first being YIN in pitch.py).
Used at full precision by the offline reference annotator, and at reduced
iteration count as a selectable real-time detector.
"""

from __future__ import annotations

import numpy as np

N_HARMONICS = 4
MIN_PROMINENCE = 4.0  # genuine spectral peak vs local floor (median of ±300 cents)
_SILENCE_RMS = 1e-5


def estimate_f0(
    frame: np.ndarray,
    sr: int,
    fmin: float = 60.0,
    fmax: float = 3000.0,
    dtft_rounds: int = 10,
) -> tuple[float | None, float]:
    """Returns (f0 in Hz or None, confidence in [0, 1])."""
    x = np.asarray(frame, dtype=np.float64)
    if np.sqrt(np.mean(x * x)) < _SILENCE_RMS:
        return None, 0.0
    x = x * np.hanning(len(x))
    nfft = 4 * len(x)  # zero-padding for interpolation resolution
    spectrum = np.abs(np.fft.rfft(x, nfft))
    bin_hz = sr / nfft

    # harmonic product spectrum (log domain = harmonic sum) for a coarse f0
    lo_bin = max(1, int(fmin / bin_hz))
    hi_bin = int(fmax / bin_hz)
    log_spec = np.log(spectrum + 1e-12)
    hps = np.zeros(hi_bin)
    for k in range(1, N_HARMONICS + 1):
        decimated = log_spec[::k]
        hps[: min(hi_bin, len(decimated))] += decimated[: min(hi_bin, len(decimated))]
    coarse_bin = lo_bin + int(np.argmax(hps[lo_bin:hi_bin]))
    coarse_hz = coarse_bin * bin_hz

    # HPS favors bins whose multiples all land on harmonics, but subharmonics
    # of the true f0 satisfy that too. A subharmonic betrays itself by having
    # no actual spectral peak at its own frequency — in that case walk up the
    # multiples until one does.
    if _peak_prominence(spectrum, coarse_hz, bin_hz) < MIN_PROMINENCE:
        for mult in (2, 3, 4):
            candidate = coarse_hz * mult
            if candidate <= fmax and _peak_prominence(spectrum, candidate, bin_hz) >= MIN_PROMINENCE:
                coarse_hz = candidate
                break

    # ...and the mirror error: when the fundamental is weak (brass and cello
    # low notes), HPS lands on harmonic k instead. Dividing f0 is justified
    # exactly when the division's extra combs — the multiples NOT shared with
    # coarse — capture substantially more of the spectral energy (i.e. real
    # partials exist between coarse's harmonics). Repeated halving/thirding
    # covers any composite division.
    while True:
        base = _comb_coverage(spectrum, coarse_hz, bin_hz)
        for div in (2, 3):
            candidate = coarse_hz / div
            if candidate >= fmin and _comb_coverage(spectrum, candidate, bin_hz) - base >= 0.1:
                coarse_hz = candidate
                break
        else:
            break

    # refine: per-harmonic maximum-likelihood frequency via continuous DTFT
    # search, then harmonic-weighted average
    estimates, weights = [], []
    for k in range(1, N_HARMONICS + 1):
        target = coarse_hz * k
        if target >= sr / 2:
            break
        peak = _interpolated_peak(spectrum, target, bin_hz)
        if peak is None:
            continue
        peak_hz, amplitude = peak
        peak_hz = _dtft_refine(x, sr, peak_hz - bin_hz, peak_hz + bin_hz, dtft_rounds)
        estimates.append(peak_hz / k)
        weights.append(amplitude)
    if not estimates:
        return None, 0.0
    f0 = float(np.average(estimates, weights=weights))

    return f0, _comb_coverage(spectrum, f0, bin_hz)


def _comb_coverage(spectrum: np.ndarray, f0_hz: float, bin_hz: float) -> float:
    """Fraction of spectral energy captured by combs at f0's multiples.

    Comb half-width covers the window mainlobe on the zero-padded grid
    (hann mainlobe = 4 analysis bins = 16 padded bins).
    """
    total = float(np.sum(spectrum**2))
    if total <= 0:
        return 0.0
    harmonic = 0.0
    half_width = 8
    # combs must be spaced wider than their own width, or "coverage" is
    # vacuously high (and a non-positive f0 would loop forever)
    if f0_hz <= 2 * half_width * bin_hz:
        return 0.0
    k = 1
    while (b := int(round(f0_hz * k / bin_hz))) + half_width < len(spectrum):
        harmonic += float(np.sum(spectrum[b - half_width : b + half_width + 1] ** 2))
        k += 1
    return min(1.0, harmonic / total)


def _interpolated_peak(
    spectrum: np.ndarray, target_hz: float, bin_hz: float, search_cents: float = 60.0
) -> tuple[float, float] | None:
    """Locate the local spectral peak near target_hz; parabolic-interpolated."""
    lo = int(target_hz * 2 ** (-search_cents / 1200) / bin_hz)
    hi = int(target_hz * 2 ** (search_cents / 1200) / bin_hz) + 1
    if lo < 1 or hi + 1 >= len(spectrum):
        return None
    i = lo + int(np.argmax(spectrum[lo:hi]))
    a, b, c = spectrum[i - 1], spectrum[i], spectrum[i + 1]
    denom = a - 2 * b + c
    offset = 0.5 * (a - c) / denom if denom < 0 else 0.0
    # a true local maximum interpolates within half a bin; anything larger
    # means i sits on a flat/noisy stretch and the parabola is meaningless
    # (unclamped, near-flat spectra have produced offsets of hundreds of bins,
    # yielding nonsense — even negative — frequencies)
    offset = max(-0.5, min(0.5, offset))
    return (i + offset) * bin_hz, float(b)


def _dtft_refine(x: np.ndarray, sr: int, f_lo: float, f_hi: float, rounds: int) -> float:
    """Maximize |DTFT(x)(f)| over continuous f by iterative grid shrinking.

    Equivalent to maximum-likelihood frequency estimation of a windowed
    sinusoid; precision is limited only by interval shrinkage (÷4 per round
    with a 9-point grid), not by any bin grid. Each round is one vectorized
    matrix product, so this stays fast enough for real-time use at low round
    counts while the offline annotator runs it to numerical exhaustion.
    """
    t = np.arange(len(x)) / sr
    points = 9
    best = (f_lo + f_hi) / 2
    for _ in range(rounds):
        freqs = np.linspace(f_lo, f_hi, points)
        magnitudes = np.abs(np.exp(-2j * np.pi * np.outer(freqs, t)) @ x)
        i = int(np.argmax(magnitudes))
        best = freqs[i]
        step = (f_hi - f_lo) / (points - 1)
        f_lo, f_hi = best - step, best + step
    return best


def _peak_prominence(spectrum: np.ndarray, target_hz: float, bin_hz: float) -> float:
    """Peak height near target_hz relative to the local spectral floor.

    Relative-to-global-max is the wrong yardstick: brass fundamentals are a
    few percent of the strongest harmonic yet perfectly real. A genuine peak
    towers over the median magnitude of its own neighborhood; a noise bump
    does not.
    """
    peak = _interpolated_peak(spectrum, target_hz, bin_hz)
    if peak is None:
        return 0.0
    peak_hz, amplitude = peak
    lo = max(1, int(peak_hz * 2 ** (-300 / 1200) / bin_hz))
    hi = min(len(spectrum), int(peak_hz * 2 ** (300 / 1200) / bin_hz))
    floor = float(np.median(spectrum[lo:hi]))
    if floor <= 0:
        return float("inf")
    return amplitude / floor
