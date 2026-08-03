"""Create a degraded copy of an audio file for robustness test fixtures.

    python -m tuner.tools.add_noise recording.wav --snr 20
    python -m tuner.tools.add_noise recording.wav --snr 15 --background other.wav

Without --background, mixes stationary white+pink noise. With --background,
mixes another recording (e.g. a different instrument, quieter) — the
realistic "tuning while someone else plays" interference case.

Writes <stem>.snr<NN>.wav (or <stem>.bg-<name>.snr<NN>.wav) next to the
input, mono. If the input has a sibling .ref.json, it is copied for the
degraded file: the clean recording's annotation is the ground truth, since
degradation hurts the annotator too.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def mix_noise(signal: np.ndarray, snr_db: float, seed: int = 0, pink_ratio: float = 0.5) -> np.ndarray:
    """Mix white+pink noise into signal at exactly the given SNR (vs signal
    power). The canonical noise model for the whole test suite — tests/synth
    delegates here so synthesized and fixture noise can never drift apart."""
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
    return signal + noise * np.sqrt(noise_power)


def mix_background(
    signal: np.ndarray, background: np.ndarray, snr_db: float
) -> np.ndarray:
    """Mix another recording under signal at the given SNR (vs signal power).
    The background is looped if shorter than the signal."""
    n = len(signal)
    reps = -(-n // len(background))  # ceil division
    bg = np.tile(background, reps)[:n]
    bg = bg / np.std(bg)

    signal_power = np.mean(signal**2)
    bg_power = signal_power / (10.0 ** (snr_db / 10.0))
    return signal + bg * np.sqrt(bg_power)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio")
    parser.add_argument("--snr", type=float, default=20.0, help="signal-to-noise ratio in dB (default 20)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--background", metavar="AUDIO",
        help="use this recording as the interference instead of white+pink noise",
    )
    parser.add_argument("-o", "--output")
    args = parser.parse_args(argv)

    audio_path = Path(args.audio)
    signal, sr = sf.read(audio_path, always_2d=True)
    mono = signal.mean(axis=1)

    if args.background:
        bg_path = Path(args.background)
        bg, bg_sr = sf.read(bg_path, always_2d=True)
        if bg_sr != sr:
            parser.error(f"sample rate mismatch: {audio_path.name}={sr}, {bg_path.name}={bg_sr}")
        noisy = mix_background(mono, bg.mean(axis=1), args.snr)
        suffix = f".bg-{bg_path.stem.split('.')[0]}.snr{args.snr:g}.wav"
    else:
        noisy = mix_noise(mono, args.snr, seed=args.seed)
        suffix = f".snr{args.snr:g}.wav"

    peak = np.max(np.abs(noisy))
    if peak > 1.0:  # keep the written file within full scale
        noisy /= peak

    output_path = Path(args.output) if args.output else audio_path.with_suffix(suffix)
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
