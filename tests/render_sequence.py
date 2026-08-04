"""Render a stitched sequence to a wav file, e.g. to listen to it with the
tuner demo:

    python -m tests.render_sequence oboe tchaik4 /tmp/tchaik4.wav
    python -m tuner.tools.demo /tmp/tchaik4.wav

Patterns: major_scale | arpeggio | chromatic | twinkle | ode_to_joy | tchaik4
(tchaik4 is oboe-only; the others use the instrument's standard root/range
from the sequence tests).
"""

from __future__ import annotations

import sys

import soundfile as sf

from tests.sequence_bank import (
    arpeggio,
    build_sequence,
    chromatic_scale,
    major_scale,
    ode_to_joy,
    tchaikovsky4_oboe,
    twinkle,
)

ROOTS = {"violin": "G4", "cello": "C2", "flute": "C5", "trumpet": "C4"}
RANGES = {
    "violin": ("G3", "A5"),
    "cello": ("C2", "A3"),
    "flute": ("C4", "C6"),
    "trumpet": ("G3", "C5"),
}


def melody_for(instrument: str, pattern: str):
    if pattern == "tchaik4":
        assert instrument == "oboe", "tchaik4 is the oboe solo"
        return tchaikovsky4_oboe()
    generic = {
        "major_scale": lambda: major_scale(ROOTS[instrument]),
        "arpeggio": lambda: arpeggio(ROOTS[instrument]),
        "chromatic": lambda: chromatic_scale(*RANGES[instrument]),
        "twinkle": lambda: twinkle(ROOTS[instrument]),
        "ode_to_joy": lambda: ode_to_joy(ROOTS[instrument]),
    }
    return generic[pattern]()


def main(argv: list[str]) -> int:
    instrument, pattern, out = argv
    signal, sr, _ref = build_sequence(instrument, melody_for(instrument, pattern))
    sf.write(out, signal, sr)
    print(f"{out}: {len(signal) / sr:.1f}s, {instrument} {pattern}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
