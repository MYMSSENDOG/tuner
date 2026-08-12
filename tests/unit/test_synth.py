"""Self-verification of the test signal synthesizer."""

import numpy as np
import pytest

from tests.synth import (
    SR,
    add_noise,
    glissando,
    glissando_freqs,
    measured_snr_db,
    sequence,
    tone,
)


def fft_peak_hz(signal: np.ndarray, sr: int = SR) -> float:
    windowed = signal * np.hanning(len(signal))
    spectrum = np.abs(np.fft.rfft(windowed))
    peak = int(np.argmax(spectrum))
    # parabolic interpolation around the peak bin
    if 0 < peak < len(spectrum) - 1:
        a, b, c = spectrum[peak - 1 : peak + 2]
        peak += 0.5 * (a - c) / (a - 2 * b + c)
    return peak * sr / len(signal)


def test_pure_tone_frequency():
    signal = tone(440.0, 1.0)
    assert fft_peak_hz(signal) == pytest.approx(440.0, abs=0.5)


def test_harmonic_tone_fundamental_present():
    signal = tone(196.0, 1.0, instrument="violin")
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    freqs = np.fft.rfftfreq(len(signal), 1 / SR)
    for k in (1, 2, 3):
        bin_idx = round(196.0 * k * len(signal) / SR)
        window = spectrum[bin_idx - 3 : bin_idx + 4]
        assert window.max() > 0.01 * spectrum.max(), f"harmonic {k} missing"
        assert freqs[bin_idx] == pytest.approx(196.0 * k, abs=1.0)


def test_snr_is_accurate():
    signal = tone(440.0, 1.0, instrument="violin")
    for target in (20.0, 10.0, 5.0):
        noisy = add_noise(signal, target)
        assert measured_snr_db(signal, noisy) == pytest.approx(target, abs=0.5)


def test_glissando_endpoints():
    freqs = glissando_freqs(200.0, 800.0, 2.0)
    assert freqs[0] == pytest.approx(200.0)
    assert freqs[-1] == pytest.approx(800.0, rel=1e-3)
    signal = glissando(200.0, 800.0, 2.0)
    assert len(signal) == len(freqs)
    # spot-check instantaneous frequency at the start via a short FFT window
    head = signal[: SR // 4]
    assert fft_peak_hz(head, SR) == pytest.approx(np.mean(freqs[: SR // 4]), rel=0.02)


def test_sequence_length_and_gaps():
    signal = sequence([440.0, 550.0], note_duration=0.5, gap=0.02)
    expected = 2 * int(0.5 * SR) + 2 * int(0.02 * SR)
    assert len(signal) == expected
    # gap region is silent
    gap_region = signal[int(0.5 * SR) : int(0.52 * SR)]
    assert np.max(np.abs(gap_region)) == 0.0
