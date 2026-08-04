"""Build the stitched-sequence note bank from raw Iowa MIS downloads.

    python -m tuner.tools.build_note_bank <raw_dir> <bank_dir>

raw_dir layout: <instrument>/<NoteLabel>.aif (e.g. violin/G3.aif, flats as
Ab/Bb/Db/Eb/Gb — Iowa's naming). For each note this trims to the sustained
stroke (keeping the natural attack), fades the edges, peak-normalizes,
writes mono FLAC into bank_dir, and annotates it with the offline reference
estimator. The manifest (bank.json) stores each clip's per-window pitch
timeline, so sequences stitched from the bank get exact ground truth without
ever re-annotating.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from tuner.analysis.reference import annotate

CLIP_MAX_S = 1.5
WINDOW_S = 0.05


def prepare_clip(mono: np.ndarray, sr: int) -> np.ndarray:
    w = int(WINDOW_S * sr)
    n = len(mono) // w
    rms = np.array([np.sqrt(np.mean(mono[i * w : (i + 1) * w] ** 2)) for i in range(n)])
    thresh = np.percentile(rms, 95) * 10 ** (-25 / 20)
    above = np.nonzero(rms > thresh)[0]
    start = max(0, above[0] * w - int(0.05 * sr))  # keep the natural attack
    end = min((above[-1] + 1) * w, start + int(CLIP_MAX_S * sr))
    clip = mono[start:end].copy()

    fade_in, fade_out = int(0.01 * sr), int(0.03 * sr)
    clip[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
    clip[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
    return clip * (0.7 / np.max(np.abs(clip)))


def main(argv: list[str] | None = None) -> int:
    raw_dir, bank_dir = (Path(p) for p in (argv or sys.argv[1:]))
    manifest: dict[str, dict] = {}
    for raw in sorted(raw_dir.glob("*/*.aif")):
        instrument, note = raw.parent.name, raw.stem
        signal, sr = sf.read(raw, always_2d=True)
        clip = prepare_clip(signal.mean(axis=1), sr)

        windows = annotate(clip, sr, window_s=WINDOW_S)
        labeled = [w.freq_hz for w in windows if w.freq_hz is not None]
        if not labeled:
            print(f"SKIP {instrument}/{note}: no confident pitch")
            continue

        out = bank_dir / instrument / f"{note}.flac"
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out, clip, sr, subtype="PCM_16")
        manifest.setdefault(instrument, {})[note] = {
            "freq_hz": float(np.median(labeled)),
            "sr": sr,
            "window_s": WINDOW_S,
            "windows": [w.freq_hz for w in windows],
        }
        print(f"{instrument}/{note}: {len(clip) / sr:.2f}s, {np.median(labeled):.2f}Hz")

    (bank_dir / "bank.json").write_text(json.dumps(manifest, indent=1))
    total = sum(len(v) for v in manifest.values())
    print(f"bank.json: {total} notes, {len(manifest)} instruments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
