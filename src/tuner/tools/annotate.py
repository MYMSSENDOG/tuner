"""Generate reference pitch annotations for an audio file.

    python -m tuner.tools.annotate recording.wav [-w 0.05] [-o recording.ref.json]

Slices the file into fixed windows and labels each with the offline
high-precision estimator. The JSON output serves as ground truth for
comparing the real-time tuner pipeline against real recordings
(tests/test_real_audio.py picks up tests/fixtures/audio/*.wav automatically).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import soundfile as sf

from tuner.analysis.reference import annotate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio", help="audio file (anything libsndfile reads: wav/flac/ogg/...)")
    parser.add_argument(
        "-w", "--window", type=float, default=0.05, metavar="SECONDS",
        help="annotation window size (default 0.05)",
    )
    parser.add_argument("-o", "--output", help="output path (default: <audio>.ref.json)")
    args = parser.parse_args(argv)

    audio_path = Path(args.audio)
    signal, sr = sf.read(audio_path, always_2d=True)
    mono = signal.mean(axis=1)
    windows = annotate(mono, sr, window_s=args.window)

    output_path = Path(args.output) if args.output else audio_path.with_suffix(".ref.json")
    output_path.write_text(
        json.dumps(
            {
                "source": audio_path.name,
                "sr": sr,
                "window_s": args.window,
                "windows": [
                    {"t0": w.t0, "t1": w.t1, "freq_hz": w.freq_hz, "confidence": w.confidence}
                    for w in windows
                ],
            },
            indent=1,
        )
    )
    labeled = sum(1 for w in windows if w.freq_hz is not None)
    print(f"{output_path}: {labeled}/{len(windows)} windows labeled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
