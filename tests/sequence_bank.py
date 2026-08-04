"""Stitch real recorded single notes into test sequences.

The bank (tests/fixtures/notes/, built by tuner.tools.build_note_bank)
holds one trimmed clip per note per instrument plus that clip's per-window
pitch timeline. Sequences concatenate clips, so their ground truth is
assembled from the bank timelines — exact, vibrato-aware, and free (no
annotation at test time).

Note labels use flats (Ab/Bb/Db/Eb/Gb), matching the Iowa file naming.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import numpy as np
import soundfile as sf

from tuner.analysis.reference import RefWindow

BANK_DIR = Path(__file__).parent / "fixtures" / "notes"
CHROMATIC = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")

Melody = list[tuple[str, float]]  # (note label, duration seconds)


@cache
def manifest() -> dict:
    return json.loads((BANK_DIR / "bank.json").read_text())


@cache
def _clip(instrument: str, note: str) -> np.ndarray:
    samples, _sr = sf.read(BANK_DIR / instrument / f"{note}.flac")
    return samples


def bank_notes(instrument: str) -> list[str]:
    notes = manifest()[instrument]
    return sorted(notes, key=lambda n: notes[n]["freq_hz"])


def transpose(label: str, semitones: int) -> str:
    idx = CHROMATIC.index(label[:-1]) + semitones
    return f"{CHROMATIC[idx % 12]}{int(label[-1]) + idx // 12}"


ATTACK_UNLABELED_S = 0.3
"""Each note's first 0.3s is kept in the audio but left unlabeled in the
ground truth: real attacks can transiently sound a different pitch (flute
register transitions pass through the octave below), so no single frequency
is the honest label there."""


def build_sequence(
    instrument: str, melody: Melody, gap_s: float = 0.04
) -> tuple[np.ndarray, int, list[RefWindow]]:
    """Returns (signal, sr, ref windows on the annotator's 0.05s grid)."""
    info = manifest()[instrument]
    sr = next(iter(info.values()))["sr"]
    window_s = next(iter(info.values()))["window_s"]
    fade = int(0.02 * sr)
    envelope = np.linspace(1.0, 0.0, fade)

    parts: list[np.ndarray] = []
    segments: list[tuple[float, float, list[float | None]]] = []  # t0, t1, clip windows
    t = 0.0
    for note, duration in melody:
        clip = _clip(instrument, note)
        n = min(len(clip), int(duration * sr))
        part = clip[:n].copy()
        part[-fade:] *= envelope  # clips already fade in at their attack
        parts.append(part)
        segments.append((t, t + n / sr, info[note]["windows"]))
        t += n / sr
        parts.append(np.zeros(int(gap_s * sr)))
        t += gap_s
    signal = np.concatenate(parts)

    ref = []
    for i in range(int(len(signal) / sr / window_s)):
        t0, t1 = i * window_s, (i + 1) * window_s
        center = (t0 + t1) / 2
        freq = None
        for seg_t0, seg_t1, clip_windows in segments:
            if seg_t0 <= center < seg_t1:
                if center - seg_t0 >= ATTACK_UNLABELED_S:
                    k = int((center - seg_t0) / window_s)
                    if k < len(clip_windows):
                        freq = clip_windows[k]
                break
        ref.append(RefWindow(t0=t0, t1=t1, freq_hz=freq, confidence=1.0 if freq else 0.0))
    return signal, sr, ref


# ---------------------------------------------------------------- patterns

MAJOR = (0, 2, 4, 5, 7, 9, 11, 12)


def major_scale(root: str, up_and_down: bool = True, dur: float = 0.7) -> Melody:
    steps = list(MAJOR) + (list(MAJOR[-2::-1]) if up_and_down else [])
    return [(transpose(root, s), dur) for s in steps]


def chromatic_scale(lo: str, hi: str, dur: float = 0.7) -> Melody:
    n = CHROMATIC.index(hi[:-1]) - CHROMATIC.index(lo[:-1]) + 12 * (int(hi[-1]) - int(lo[-1]))
    return [(transpose(lo, s), dur) for s in range(n + 1)]


def arpeggio(root: str, up_and_down: bool = True, dur: float = 0.7) -> Melody:
    steps = [0, 4, 7, 12] + ([7, 4, 0] if up_and_down else [])
    return [(transpose(root, s), dur) for s in steps]


def from_degrees(root: str, degrees: list[int], durs: list[float]) -> Melody:
    return [(transpose(root, MAJOR[d % 7] + 12 * (d // 7)), t) for d, t in zip(degrees, durs)]


def twinkle(root: str) -> Melody:
    degrees = [0, 0, 4, 4, 5, 5, 4, 3, 3, 2, 2, 1, 1, 0]
    durs = [0.6] * 6 + [1.0] + [0.6] * 6 + [1.0]
    return from_degrees(root, degrees, durs)


def ode_to_joy(root: str) -> Melody:
    degrees = [2, 2, 3, 4, 4, 3, 2, 1, 0, 0, 1, 2, 2, 1, 1]
    durs = [0.6] * 12 + [0.8, 0.6, 1.0]
    return from_degrees(root, degrees, durs)
