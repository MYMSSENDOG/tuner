"""Test signal synthesizer.

All test audio is synthesized so the ground-truth frequency is known exactly,
which is what makes cent-level assertions possible.
"""

from __future__ import annotations

import numpy as np

SR = 44100

# Relative harmonic amplitudes (fundamental first) roughly mimicking timbres.
INSTRUMENT_PROFILES: dict[str, tuple[float, ...]] = {
    "pure": (1.0,),
    "violin": (1.0, 0.75, 0.55, 0.45, 0.35, 0.25, 0.18, 0.12, 0.08, 0.05),
    "cello": (1.0, 0.9, 0.6, 0.5, 0.3, 0.2, 0.1),
    "flute": (1.0, 0.25, 0.08, 0.03),
    "guitar": (1.0, 0.6, 0.4, 0.25, 0.15, 0.08),
    "voice": (1.0, 0.5, 0.7, 0.3, 0.2, 0.1),
}


def _phase_from_freq(freq_hz: np.ndarray, sr: int) -> np.ndarray:
    return 2.0 * np.pi * np.cumsum(freq_hz) / sr


def _render(freq_hz: np.ndarray, harmonics: tuple[float, ...], sr: int) -> np.ndarray:
    phase = _phase_from_freq(freq_hz, sr)
    signal = np.zeros_like(phase)
    nyquist = sr / 2.0
    for k, amp in enumerate(harmonics, start=1):
        if np.max(freq_hz) * k >= nyquist:
            break
        signal += amp * np.sin(k * phase)
    peak = np.max(np.abs(signal))
    return signal / peak if peak > 0 else signal


def tone(
    freq_hz: float,
    duration: float,
    instrument: str = "pure",
    vibrato_cents: float = 0.0,
    vibrato_hz: float = 5.5,
    sr: int = SR,
) -> np.ndarray:
    n = int(duration * sr)
    freq = np.full(n, float(freq_hz))
    if vibrato_cents > 0:
        t = np.arange(n) / sr
        freq = freq * 2.0 ** (vibrato_cents * np.sin(2 * np.pi * vibrato_hz * t) / 1200.0)
    return _render(freq, INSTRUMENT_PROFILES[instrument], sr)


def glissando(
    f_start: float,
    f_end: float,
    duration: float,
    instrument: str = "pure",
    sr: int = SR,
) -> np.ndarray:
    """Exponential (constant cents/sec) sweep from f_start to f_end."""
    n = int(duration * sr)
    freq = f_start * (f_end / f_start) ** (np.arange(n) / n)
    return _render(freq, INSTRUMENT_PROFILES[instrument], sr)


def glissando_freqs(f_start: float, f_end: float, duration: float, sr: int = SR) -> np.ndarray:
    """Ground-truth instantaneous frequency of glissando() per sample."""
    n = int(duration * sr)
    return f_start * (f_end / f_start) ** (np.arange(n) / n)


def sequence(
    freqs: list[float],
    note_duration: float = 0.5,
    gap: float = 0.02,
    instrument: str = "pure",
    sr: int = SR,
) -> np.ndarray:
    """Discrete notes (scale/arpeggio) with short silent gaps and attack/release ramps."""
    parts = []
    ramp_n = int(0.005 * sr)
    envelope_ramp = np.linspace(0.0, 1.0, ramp_n)
    for f in freqs:
        note = tone(f, note_duration, instrument=instrument, sr=sr)
        note[:ramp_n] *= envelope_ramp
        note[-ramp_n:] *= envelope_ramp[::-1]
        parts.append(note)
        parts.append(np.zeros(int(gap * sr)))
    return np.concatenate(parts)


def add_noise(signal: np.ndarray, snr_db: float, seed: int = 0, pink_ratio: float = 0.5) -> np.ndarray:
    """Mix in white+pink noise at the given SNR (relative to signal power)."""
    rng = np.random.default_rng(seed)
    n = len(signal)
    white = rng.standard_normal(n)
    # pink noise: shape white noise spectrum by 1/sqrt(f)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0)
    f[0] = f[1] if len(f) > 1 else 1.0
    pink = np.fft.irfft(spectrum / np.sqrt(f), n)
    pink /= np.std(pink)
    noise = (1 - pink_ratio) * white + pink_ratio * pink
    noise /= np.std(noise)

    signal_power = np.mean(signal**2)
    noise_power = signal_power / (10.0 ** (snr_db / 10.0))
    return signal + noise * np.sqrt(noise_power)


def measured_snr_db(signal: np.ndarray, noisy: np.ndarray) -> float:
    noise = noisy - signal
    return 10.0 * np.log10(np.mean(signal**2) / np.mean(noise**2))
