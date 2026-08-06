"""Display steadiness on real recordings — the user-visible failure mode.

A tuner that reports the right pitch but repaints a different note name
several times per second is unusable. These tests bound how much the
*displayed* note is allowed to move on recordings whose note content is
known, which is what the meter's stabilization machinery (input gate,
tracker jump confirmation, note latch) exists to guarantee.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
import soundfile as sf

from tuner.app.engine import TunerEngine, TunerReading

from tests.fakes import FakeAudioInput

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"


def displayed_labels(path: Path) -> list[str | None]:
    """Note labels the app would show, in order, for a whole recording."""
    signal, sr = sf.read(path, always_2d=True)
    fake = FakeAudioInput(signal.mean(axis=1), sr=sr)
    readings: list[TunerReading] = []
    engine = TunerEngine(fake, readings.append)
    engine.start()
    fake.pump()
    engine.stop()
    return [r.note.label if r.note is not None else None for r in readings]


def label_segments(labels: list[str | None]) -> list[str]:
    """Consecutive runs of the same displayed name, silence removed."""
    return [label for label, _ in itertools.groupby(l for l in labels if l is not None)]


# (file, notes actually played, allowed extra segments)
# The allowance covers attack transients, where an instrument genuinely
# sounds another pitch briefly (flute register transitions) — but it is
# small enough that flicker regressions fail loudly.
CASES = [
    ("violin_scale_G3B3.aiff", 5, 1),
    ("violin_arco_A4.aif", 1, 1),
    ("violin_arco_G3.snr20.wav", 1, 1),
    ("flute_vib_C6.aif", 1, 2),
    ("trumpet_vib_A4.aif", 1, 1),
    ("trumpet_novib_G3.bg-flute_vib_C6.snr15.wav", 1, 2),
    ("cello_arco_A3.aif", 1, 1),
    ("oboe_scale_C4B4.aiff", 12, 4),
]


@pytest.mark.parametrize(
    "filename,played,allowance", CASES, ids=[c[0].split(".")[0] for c in CASES]
)
def test_display_does_not_flicker(filename, played, allowance):
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    segments = label_segments(displayed_labels(path))
    print(f"\n{filename}: {len(segments)} segments (played {played}) {segments[:12]}")
    assert len(segments) <= played + allowance, f"display flicker: {segments}"


def test_stabilization_is_actually_doing_something(monkeypatch):
    """Power check: with the note latch off, a vibrato note that straddles a
    semitone boundary must flicker — proving the test above can fail and the
    latch is what prevents it."""
    from tuner.core import notes

    path = FIXTURE_DIR / "flute_vib_C6.aif"
    if not path.exists():
        pytest.skip("fixture not present")

    with_latch = len(label_segments(displayed_labels(path)))
    monkeypatch.setattr(notes, "NOTE_LATCH_ENABLED", False)
    without_latch = len(label_segments(displayed_labels(path)))
    print(f"\nflute segments: latch on {with_latch}, off {without_latch}")
    assert without_latch >= with_latch + 5
