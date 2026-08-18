"""Measurements the suite already makes, kept instead of thrown away.

Nearly every gate here computes more than it asserts — `assert_pipeline_agreement`
works out median/p90/p95 and an octave-miss rate, then prints them and lets
them evaporate. What survived a run was one bit: green or red. Since the
thresholds sit just above the worst measured fixture, everything below them
could drift for months without a test noticing.

So: the same numbers, recorded under stable names, one file per run. The
gates are unchanged and stay the only thing that can fail a run — this is an
observation layer, not a second set of thresholds
(`python -m tuner.tools.scoreboard`, docs/dev-loop.md).

Under `-n auto` each xdist worker is its own process with its own buffer, so
every process writes its own file into the run's directory and the reader
merges by globbing. Nothing has to be handed back to the controller.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "metrics"
BETTER = ("lower", "higher")


@dataclass(frozen=True)
class Measurement:
    name: str  # scoreboard row: must stay stable across runs
    value: float
    unit: str = ""
    better: str = "lower"  # which direction counts as an improvement


@dataclass
class Recorder:
    taken: list[Measurement] = field(default_factory=list)

    def record(
        self, name: str, value: float, *, unit: str = "", better: str = "lower"
    ) -> float:
        """Keep one measurement; returns it so call sites can stay one-liners."""
        if better not in BETTER:
            raise ValueError(f"better must be one of {BETTER}, got {better!r}")
        self.taken.append(Measurement(name, float(value), unit, better))
        return value

    def write(self, run_id: str, part: str, base: Path | None = None) -> Path | None:
        """One file per process, named after the xdist worker that filled it."""
        if not self.taken:
            return None
        directory = runs_dir(base) / run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{part}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for measurement in self.taken:
                fh.write(json.dumps(asdict(measurement), ensure_ascii=False) + "\n")
        return path


def runs_dir(base: Path | None = None) -> Path:
    """Where runs are stored. TUNER_METRICS_DIR keeps tests off the real one."""
    root = base or Path(os.environ.get("TUNER_METRICS_DIR") or DEFAULT_DIR)
    return root / "runs"


SUITE = Recorder()


def record(name: str, value: float, *, unit: str = "", better: str = "lower") -> float:
    return SUITE.record(name, value, unit=unit, better=better)
