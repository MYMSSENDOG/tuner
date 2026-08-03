"""Create a noisy copy of an audio file for robustness test fixtures.

    python -m tuner.tools.add_noise recording.wav --snr 20

Writes <stem>.snr<NN>.wav next to the input (mono). If the input has a
sibling .ref.json, it is copied for the noisy file: the clean recording's
annotation is the ground truth for its noisy variant, since noise degrades
the annotator too.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def mix_noise(signal: np.ndarray, snr_db: float, seed: int = 0, pink_ratio: float = 0.5) -> np.ndarray:
    """Mix white+pink noise into signal at the given SNR (vs signal power)."""
    rng = np.random.default_rng(seed)
    n = len(signal)
    white = rng.standard_normal(n)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0)
    f[0] = f[1] if len(f) > 1 else 1.0
    pink = np.fft.irfft(spectrum / np.sqrt(f), n)
    pink /= np.std(pink)
    noise = (1 - pink_ratio) * white + pink_ratio * pink
    noise /= np.std(noise)

    signal_power = np.mean(signal**2)
    noise_power = signal_power / (10.0 ** (snr_db / 10.0))
    noisy = signal + noise * np.sqrt(noise_power)
    peak = np.max(np.abs(noisy))
    return noisy / peak if peak > 1.0 else noisy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio")
    parser.add_argument("--snr", type=float, default=20.0, help="signal-to-noise ratio in dB (default 20)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("-o", "--output")
    args = parser.parse_args(argv)

    audio_path = Path(args.audio)
    signal, sr = sf.read(audio_path, always_2d=True)
    mono = signal.mean(axis=1)
    noisy = mix_noise(mono, args.snr, seed=args.seed)

    output_path = (
        Path(args.output)
        if args.output
        else audio_path.with_suffix(f".snr{args.snr:g}.wav")
    )
    sf.write(output_path, noisy, sr)
    print(f"{output_path}: SNR {args.snr:g}dB")

    ref = audio_path.with_suffix(".ref.json")
    if ref.exists():
        noisy_ref = output_path.with_suffix(".ref.json")
        shutil.copy(ref, noisy_ref)
        print(f"{noisy_ref}: copied from clean annotation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
