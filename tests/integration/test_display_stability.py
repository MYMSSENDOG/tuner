"""Display steadiness on real recordings — the user-visible failure mode.

A tuner that reports the right pitch but repaints a different note name
several times per second is unusable. These tests bound how much the
*displayed* note is allowed to move on recordings whose note content is
known, which is what the meter's stabilization machinery (input gate,
tracker jump confirmation, note latch) exists to guarantee.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.metrics import record
from tuner.tools.trace import (
    BRIEF_FRAMES,
    TraceFrame,
    brief_flashes,
    label_segments,
    trace_file,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "audio"


# The tracer runs the same engine over the same blocks these tests used to
# assemble by hand, and it defines the display metrics in one place so that
# the dev tool (python -m tuner.tools.trace) and this suite cannot drift
# apart — a metric measured twice is a metric nobody can trust.


def displayed_frames(path: Path) -> list[TraceFrame]:
    """Every frame the app would display, in order, for a whole recording."""
    return trace_file(path).frames


def displayed_labels(path: Path) -> list[str | None]:
    """Note labels the app would show, in order, for a whole recording."""
    return trace_file(path).labels


# (file, notes actually played, allowed extra segments)
#
# A segment count is a runaway cap, not a fine seal. Under the +-50 rule a
# boundary-straddling vibrato legitimately alternates names, so on some
# fixtures this number is identical with the latch on and off — it can still
# catch a display that repaints wildly, but it cannot tell whether the latch
# works. That job belongs to test_no_name_flashes_briefly below, which counts
# only the runs too short to read.
#
# Allowances therefore sit a little above the measured value rather than
# exactly on it: three of these used to pass with zero slack, so any unrelated
# change (a detector tweak, a numpy version) broke them for no reason.
# === driver: measured latch-on segments are 5 / 1 / 1 / 15 / 2 / 2 / 2 / 13.
CASES = [
    ("violin_scale_G3B3.aiff", 5, 1),
    ("violin_arco_A4.aif", 1, 1),
    ("violin_arco_G3.snr20.wav", 1, 1),
    # 17: this note vibrates across a semitone boundary (C6 +30..+70c), and
    # the rule that the number never leaves +-50 means the name follows the
    # pitch to whichever side it is on (measured 15, and 15 with the latch off
    # too). Was 2 while the name was held past the boundary instead - a
    # motionless "+50" for up to 75ms per cycle.
    # === driver: docs/note-latch-tuning.md "+-50 rule" - product decision.
    ("flute_vib_C6.aif", 1, 17),
    ("trumpet_vib_A4.aif", 1, 2),
    ("trumpet_novib_G3.bg-flute_vib_C6.snr15.wav", 1, 2),
    ("cello_arco_A3.aif", 1, 2),
    ("oboe_scale_C4B4.aiff", 12, 4),
]

# BRIEF_FRAMES (8 frames = 46ms) lives in tuner/tools/trace.py with its
# rationale: a name painted for fewer frames than that is not readable as a
# note, it is a flash, and genuine alternation is far slower.
# Measured latch-on counts are 0 everywhere except one flash each on
# flute_vib_C6 and trumpet_vib_A4 (both attack transients), so 2 leaves every
# fixture a frame of slack. With the latch off the interference fixture jumps
# to 6 — see test_brief_flash_bound_is_actually_doing_something.
MAX_BRIEF_FLASHES = 2


@pytest.mark.parametrize(
    "filename", [c[0] for c in CASES], ids=[c[0].split(".")[0] for c in CASES]
)
def test_no_name_flashes_briefly(filename):
    """The seal a segment count cannot provide: names that appear too briefly
    to read. Alternation the +-50 rule requires is slow enough to pass; the
    octave glitches the latch exists to absorb are not.
    """
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    flashes = brief_flashes(displayed_labels(path))
    print(f"\n{filename}: {flashes} runs shorter than {BRIEF_FRAMES} frames")
    record(f"display/{filename}/brief_flashes", flashes)
    assert flashes <= MAX_BRIEF_FLASHES


def test_brief_flash_bound_is_actually_doing_something(monkeypatch):
    """Power check: with the latch off, the interference fixture flashes names
    it cannot read. This is where the latch's measured value actually sits —
    on most fixtures latch on and off score the same.
    """
    from tuner.core import notes

    path = FIXTURE_DIR / "trumpet_novib_G3.bg-flute_vib_C6.snr15.wav"
    if not path.exists():
        pytest.skip("fixture not present")

    with_latch = brief_flashes(displayed_labels(path))
    monkeypatch.setattr(notes, "NOTE_LATCH_ENABLED", False)
    without_latch = brief_flashes(displayed_labels(path))
    print(f"\ninterference brief flashes: latch on {with_latch}, off {without_latch}")
    assert with_latch <= MAX_BRIEF_FLASHES < without_latch


@pytest.mark.parametrize(
    "filename,played,allowance", CASES, ids=[c[0].split(".")[0] for c in CASES]
)
def test_display_does_not_flicker(filename, played, allowance):
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    segments = label_segments(displayed_labels(path))
    print(f"\n{filename}: {len(segments)} segments (played {played}) {segments[:12]}")
    record(f"display/{filename}/segments", len(segments))
    assert len(segments) <= played + allowance, f"display flicker: {segments}"


# --- attacks over a tonal hum: what a real room does to the display ---
#
# Field report (2026-08-20, 36s of real playing into a room mic): the noise
# floor sat at -39dBFS, just above the -40 input gate, and it was *tonal* —
# a ~123Hz hum the detector locked onto as B2 with confidence ~0.9. So the
# latch always held a name, and every note attack had to wait out its dwell
# before the display followed: detector right after a median of 20ms, display
# after 81ms, and in between the meter showed the hum's name pinned at +-50.
#
# White noise does not reproduce this (the detector rejects it outright, the
# tracker reports nothing, and the latch resets). The hum is the mechanism.
HUM_HZ = 123.47  # B2-ish, measured off the field recording
HUM_DBFS = -32.0  # above the gate, as measured; -45 leaves it below
ATTACK_NOTES = (("F", 5), ("D", 5))

# What the player is allowed to be shown at an attack: the right name, or an
# admission that we do not know yet. What must never happen is a confident
# wrong reading — a name from before the note, parked at the end of the scale.
# === driver: the attack dwell is 6 frames, so the display can be 6 frames
# === (35ms) behind the detector; measured worst is exactly that. The bound
# === sits one frame above it. Before the attack dwell existed this was 70ms.
MAX_ATTACK_LAG_MS = 41.0  # detection to display; one hop is 5.8ms


def hum_then_notes(gap_s: float = 1.0, note_s: float = 0.8):
    """A tonal hum running under two notes, with silence-plus-hum between."""
    import numpy as np

    from tests.synth import SR, add_noise, tone
    from tuner.core.notes import note_to_freq

    quiet = np.zeros(int(gap_s * SR))
    notes = [tone(note_to_freq(n, o), note_s, instrument="flute") for n, o in ATTACK_NOTES]
    body = np.concatenate([quiet, notes[0], quiet, notes[1], quiet])
    hum = tone(HUM_HZ, len(body) / SR + 0.05, instrument="pure")[: len(body)]
    hum = hum / np.sqrt(np.mean(hum**2)) * 10 ** (HUM_DBFS / 20)
    return add_noise(body + hum, 30.0, seed=11), SR


def attack_lag_ms(trace) -> float:
    """Worst gap between the detector being right and the display agreeing."""
    from tuner.core.notes import freq_to_note

    worst = 0.0
    for onset, (name, octave) in zip((1.0, 2.8), ATTACK_NOTES, strict=True):
        target = f"{name}{octave}"
        window = [f for f in trace.frames if onset <= f.t_s <= onset + 0.5]
        detected = next(
            (f for f in window if f.raw_hz and freq_to_note(f.raw_hz).label == target), None
        )
        shown = next((f for f in window if f.label == target), None)
        assert detected and shown, f"{target}: never reached the display"
        worst = max(worst, 1000.0 * (shown.t_s - detected.t_s))
    return worst


def test_attack_release_is_actually_doing_something(monkeypatch):
    """Power check: with the attack release off, the display sits out the full
    dwell before following a note that the detector already had."""
    from tuner.app import engine as engine_mod
    from tuner.tools.trace import trace_signal

    signal, sr = hum_then_notes()
    with_release = attack_lag_ms(trace_signal(signal, sr))
    monkeypatch.setattr(engine_mod, "ATTACK_RELEASE_ENABLED", False)
    without_release = attack_lag_ms(trace_signal(signal, sr))
    print(f"\n어택 지연: 해제 켜짐 {with_release:.0f}ms, 꺼짐 {without_release:.0f}ms")
    record("display/attack_lag_ms", with_release, unit="ms")
    assert with_release <= MAX_ATTACK_LAG_MS < without_release


def test_attack_never_shows_the_previous_name_as_a_reading():
    """The seal on the field finding: at a note onset the meter may lag, but
    it may not spend that lag asserting the hum's note at the meter's edge.
    """
    from tests.synth import SR
    from tuner.core.notes import freq_to_note
    from tuner.tools.trace import trace_signal

    signal, sr = hum_then_notes()
    trace = trace_signal(signal, sr)
    onsets = [1.0, 2.8]  # gap, note, gap, note

    for onset, (name, octave) in zip(onsets, ATTACK_NOTES, strict=True):
        target = f"{name}{octave}"
        window = [f for f in trace.frames if onset <= f.t_s <= onset + 0.5]
        detected = next(
            (f for f in window if f.raw_hz and freq_to_note(f.raw_hz).label == target), None
        )
        assert detected is not None, f"{target}: never detected"
        wrong = [
            f
            for f in window
            if f.t_s > detected.t_s + MAX_ATTACK_LAG_MS / 1000.0
            and f.label not in (None, target)
            and f.cents is not None
        ]
        print(
            f"\n{target}: 검출 {1000 * (detected.t_s - onset):.0f}ms, "
            f"그 뒤 {len(wrong)}프레임이 다른 이름을 표시 "
            f"({sorted({f.label for f in wrong})})"
        )
        assert not wrong, (
            f"{target} 검출 후 {MAX_ATTACK_LAG_MS:.0f}ms 넘게 "
            f"{sorted({f.label for f in wrong})} 를 {wrong[0].cents:+.0f}c 로 표시했다 "
            f"({len(wrong)}프레임, {SR and len(wrong) * 256 / SR * 1000:.0f}ms)"
        )


# Recordings whose note content changes, i.e. where the display has to cross
# semitone boundaries for real. Scales exercise leaps the single-note
# fixtures never reach.
CENTS_RANGE_CASES = [
    "violin_scale_G3B3.aiff",
    "oboe_scale_C4B4.aiff",
    "flute_scale_B3B4.aiff",
    "cello_scale_A2Gb3.aiff",
    "flute_vib_C6.aif",
    "trumpet_vib_A4.aif",
]

# A semitone of "deviation" is a different note, not a deviation. The meter
# reads -50..+50; the latch may sit past that while it holds a boundary
# wobble, but never by a whole semitone.
MAX_DISPLAYED_CENTS = 100.0

# The real bound: the meter reads -50..+50 and the number never leaves it.
# Past the boundary the pitch belongs to the neighbouring note and is shown
# there instead. MAX_DISPLAYED_CENTS above only survives for the power check,
# which needs headroom above this to tell 'clamped' from 'not clamped'.
METER_RANGE = 50.0


@pytest.mark.parametrize(
    "filename", CENTS_RANGE_CASES, ids=[c.split(".")[0] for c in CENTS_RANGE_CASES]
)
def test_displayed_cents_stay_in_range(filename):
    """No reading may claim the instrument is a semitone or more off.

    A note change used to be shown as its full interval on the previous
    name (a two-octave leap read +2400c) until the latch caught up.
    """
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    shown = [f for f in displayed_frames(path) if f.cents is not None]
    assert shown, "no readings produced"
    worst = max(shown, key=lambda f: abs(f.cents))
    print(f"\n{filename}: worst {worst.label} {worst.cents:+.0f}c of {len(shown)} readings")
    record(f"display/{filename}/worst_cents", abs(worst.cents))
    assert abs(worst.cents) <= METER_RANGE, (
        f"displayed {worst.label} {worst.cents:+.0f}c - off the meter's scale"
    )


# Readings whose number is stuck at the ceiling: the needle is pinned there
# too, so nothing on screen moves. A pitch parked just past the boundary is
# exactly what tuning an out-of-tune instrument looks like, so this cannot be
# allowed to last — it read as the meter having stopped working.
FROZEN_FRAMES_ALLOWED = 40  # ~230ms; worst measured is 16 frames / 93ms


def longest_frozen_run(frames: list[TraceFrame]) -> int:
    from tuner.core.notes import NOTE_HOLD_CENTS_CEILING

    longest = run = 0
    for frame in frames:
        if frame.cents is not None and abs(frame.cents) >= NOTE_HOLD_CENTS_CEILING - 0.5:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


@pytest.mark.parametrize(
    "filename", CENTS_RANGE_CASES, ids=[c.split(".")[0] for c in CENTS_RANGE_CASES]
)
def test_display_does_not_freeze(filename):
    path = FIXTURE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    frozen = longest_frozen_run(displayed_frames(path))
    print()
    print(f"{filename}: longest frozen run {frozen} frames")
    record(f"display/{filename}/frozen_frames", frozen)
    assert frozen <= FROZEN_FRAMES_ALLOWED


def test_freeze_bound_is_actually_doing_something(monkeypatch):
    """Power check: with the release window shut, the same recording freezes
    for far longer. The pitch there genuinely wanders +52..+68, so holding the
    old name is what pins the number at the end of the scale.
    """
    from tuner.app import engine as engine_mod
    from tuner.core import notes as notes_mod

    path = FIXTURE_DIR / "cello_scale_A2Gb3.aiff"
    if not path.exists():
        pytest.skip("fixture not present")

    class _NoRelease(notes_mod.NoteLatch):
        """Only the release window is disabled - the dwell keeps
        behaving normally, or this would measure a differently-broken latch."""

        def __init__(self, *args, **kwargs):
            kwargs["release_low_cents"] = float("inf")
            super().__init__(*args, **kwargs)

    now = longest_frozen_run(displayed_frames(path))
    monkeypatch.setattr(engine_mod, "NoteLatch", _NoRelease)
    held = longest_frozen_run(displayed_frames(path))
    print()
    print(f"cello scale longest frozen run: now {now}, without release {held}")
    assert held > FROZEN_FRAMES_ALLOWED >= now


def test_cents_ceiling_is_actually_doing_something(monkeypatch):
    """Power check: without the hold ceiling the same recording must report a
    wild deviation — proving the test above can fail and the ceiling is what
    prevents it."""
    from tuner.app import engine as engine_mod
    from tuner.core import notes as notes_mod

    path = FIXTURE_DIR / "oboe_scale_C4B4.aiff"
    if not path.exists():
        pytest.skip("fixture not present")

    class _NoCeiling(notes_mod.NoteLatch):
        """Only the clamp is disabled — the dwell must keep behaving normally,
        or this would measure a differently-broken latch instead."""

        def __init__(self, *args, **kwargs):
            kwargs["ceiling_cents"] = float("inf")
            super().__init__(*args, **kwargs)

    bounded = max(abs(f.cents) for f in displayed_frames(path) if f.cents is not None)
    monkeypatch.setattr(engine_mod, "NoteLatch", _NoCeiling)
    unbounded = max(abs(f.cents) for f in displayed_frames(path) if f.cents is not None)
    print(f"\noboe scale worst cents: ceiling on {bounded:.0f}c, off {unbounded:.0f}c")
    assert bounded < MAX_DISPLAYED_CENTS <= unbounded


def test_stabilization_is_actually_doing_something(monkeypatch):
    """Power check: with the note latch off, a recording where the detector
    jumps an octave under interference must flicker, proving the test above
    can fail and the latch is what prevents it.

    This used to point at the vibrato flute. It no longer can: under the +-50
    rule the name follows a boundary-straddling vibrato by design, so latch
    and no-latch score the same there. What the latch still buys is exactly
    this - suppressing octave glitches under interference.
    """
    from tuner.core import notes

    path = FIXTURE_DIR / "trumpet_novib_G3.bg-flute_vib_C6.snr15.wav"
    if not path.exists():
        pytest.skip("fixture not present")

    with_latch = len(label_segments(displayed_labels(path)))
    monkeypatch.setattr(notes, "NOTE_LATCH_ENABLED", False)
    without_latch = len(label_segments(displayed_labels(path)))
    print(f"\nflute segments: latch on {with_latch}, off {without_latch}")
    assert without_latch >= with_latch + 5
