"""The record of what the meter displayed: frames, storage, display metrics.

One format with two producers — `tools/trace.py` replays a recording through
the engine offline, and `app/capture.py` keeps the live one from the app — so
a field report and a fixture replay are the same kind of file and the same
tools read both.

Only the record lives here. Producing traces needs the engine, and comparing
them is a development question; both sit above this in `app/` and `tools/`.
"""

from __future__ import annotations

import itertools
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# A name painted for fewer frames than this is not readable as a note, it is a
# flash. Genuine alternation is far slower: the flute's boundary vibrato holds
# each side for a median of 14 frames (81ms), a vibrato cycle being ~180ms.
# Detection glitches are what land under 8.
BRIEF_FRAMES = 8  # 46ms at the default hop


@dataclass(frozen=True)
class TraceFrame:
    """One detection's worth of pipeline state, as the app produced it."""

    i: int
    t_s: float  # frame end — the moment this reading could exist in real time
    raw_hz: float | None  # detector output, before the display policy
    confidence: float
    hz: float | None  # what the tracker decided to display
    label: str | None  # the note name on screen
    cents: float | None  # deviation as shown (latch-clamped, not raw)
    state: str


@dataclass(frozen=True)
class Trace:
    audio: str
    sr: int
    detector: str
    a4_hz: float
    rev: str  # the code that produced it
    frames: list[TraceFrame]

    @property
    def labels(self) -> list[str | None]:
        return [f.label for f in self.frames]


# ------------------------------------------------------------------ metrics
# One definition of "how steady was the display", shared by the dev tools and
# the test suite — a metric measured twice is a metric nobody can trust.


def label_runs(labels: list[str | None]) -> list[tuple[str, int]]:
    """Consecutive runs of the same displayed name, with length, silence out."""
    return [
        (label, len(list(group)))
        for label, group in itertools.groupby(x for x in labels if x is not None)
    ]


def label_segments(labels: list[str | None]) -> list[str]:
    return [label for label, _ in label_runs(labels)]


def brief_flashes(labels: list[str | None], limit: int = BRIEF_FRAMES) -> int:
    """How many displayed names lasted fewer than `limit` readings."""
    return sum(1 for _, length in label_runs(labels) if length < limit)


# ------------------------------------------------------------------ storage


def write_jsonl(trace: Trace, path: str | Path) -> None:
    """A header line of metadata, then one line per frame."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "audio": trace.audio,
        "sr": trace.sr,
        "detector": trace.detector,
        "a4_hz": trace.a4_hz,
        "rev": trace.rev,
        "frames": len(trace.frames),
    }
    with out.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for frame in trace.frames:
            fh.write(json.dumps(frame.__dict__, ensure_ascii=False) + "\n")


def read_jsonl(path: str | Path) -> Trace:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    return Trace(
        audio=meta["audio"],
        sr=meta["sr"],
        detector=meta["detector"],
        a4_hz=meta["a4_hz"],
        rev=meta["rev"],
        frames=[TraceFrame(**json.loads(line)) for line in lines[1:] if line.strip()],
    )


def code_revision(root: Path | None = None) -> str:
    """Short sha of the code being run, with '+' when the tree is dirty.

    A trace without this is a measurement of unknown code — the first thing
    anyone asks of a field report is which build produced it.
    """
    root = root or Path(__file__).resolve().parents[3]

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        sha = git("rev-parse", "--short", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return sha + ("+" if dirty else "")
