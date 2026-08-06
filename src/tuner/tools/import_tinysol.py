"""Import a subset of the TinySOL dataset as externally-labelled fixtures.

    python -m tuner.tools.import_tinysol <extracted_dir> <metadata.csv> <out_dir>

TinySOL (Cella et al., IRCAM; CC-BY-4.0, https://zenodo.org/records/3659365)
is 14 instruments of isolated notes whose pitch labels were assigned by the
dataset authors — ground truth that owes nothing to our estimator. This
copies the chosen clips into out_dir as mono FLAC and writes labels.json
mapping each clip to its labelled note, so tests can grade against a third
party's annotations.

Clips marked "Resampled" in the metadata are skipped: those pitches were
never played, they were produced by digitally transposing another recording,
so they are not evidence about real instrument sound.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

MAX_SECONDS = 2.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio_dir", help="directory holding the extracted TinySOL tree")
    parser.add_argument("metadata", help="TinySOL_metadata.csv")
    parser.add_argument("out_dir")
    parser.add_argument("--per-instrument", type=int, default=12, help="clips per instrument")
    args = parser.parse_args(argv)

    audio_dir, out_dir = Path(args.audio_dir), Path(args.out_dir)
    rows = list(csv.DictReader(open(args.metadata)))
    by_instrument: dict[str, list[dict]] = {}
    for row in rows:
        if row["Resampled"].strip().lower() == "true":
            continue
        source = next(audio_dir.glob(f"**/{Path(row['Path']).name}"), None)
        if source is None:
            continue
        by_instrument.setdefault(row["Instrument (in full)"], []).append({**row, "src": source})

    labels: dict[str, dict] = {}
    for instrument, entries in sorted(by_instrument.items()):
        entries.sort(key=lambda e: int(e["Pitch ID"]))
        # spread the picks across the instrument's range instead of taking
        # the lowest N: register is exactly what stresses a pitch detector
        step = max(1, len(entries) // args.per_instrument)
        for entry in entries[::step][: args.per_instrument]:
            signal, sr = sf.read(entry["src"], always_2d=True)
            mono = signal.mean(axis=1)[: int(MAX_SECONDS * sr)]
            peak = float(np.max(np.abs(mono)))
            if peak > 0:
                mono = mono * (0.7 / peak)
            name = f"{entry['Instrument (abbr.)']}-{entry['Pitch']}".replace("#", "s")
            out = out_dir / f"{name}.flac"
            out.parent.mkdir(parents=True, exist_ok=True)
            sf.write(out, mono, sr, subtype="PCM_16")
            labels[out.name] = {
                "instrument": instrument,
                "pitch": entry["Pitch"],
                "midi": int(entry["Pitch ID"]),
                "dynamics": entry["Dynamics"],
                "source": entry["Path"],
            }
        print(f"{instrument}: {min(len(entries), args.per_instrument)} clips")

    (out_dir / "labels.json").write_text(json.dumps(labels, indent=1, sort_keys=True))
    print(f"{out_dir}/labels.json: {len(labels)} clips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
