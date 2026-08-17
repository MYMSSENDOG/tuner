"""Hz <-> (note name, octave, cents) conversion, parameterized by A4 reference."""

from __future__ import annotations

import math
from dataclasses import dataclass

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

A4_MIDI = 69

# How far past the halfway point a pitch must travel before the displayed
# note name changes. Without it, an instrument sitting near a semitone
# boundary — exactly what happens when it is badly out of tune, i.e. while
# you are tuning it — flickers between two names on every vibrato cycle.
# === display switch: set False for the raw nearest note every frame ===
NOTE_LATCH_ENABLED = True

NOTE_HYSTERESIS_CENTS = 15.0
NOTE_DWELL_FRAMES = 12  # ~70ms at the default hop; measured against vibrato

# Ceiling on the deviation a *held* note may report. While holding, cents are
# measured from the name on screen, so a pitch that moved somewhere else
# entirely produced absurd readings (a two-octave leap read +2400c on the note
# we left, and octave glitches under interference read past 1200c). The hold
# itself has to stay - it is what stops the name flickering when detection
# oscillates - so only the number is bounded.
# Set to the meter's own range so the badge never claims something the needle
# cannot show: past this the needle is pinned at METER_RANGE anyway.
# === driver: docs/note-latch-tuning.md - releasing the hold early instead was
# === measured and rejected (broke flicker sealing on 2 fixtures). 65c (the
# === hysteresis edge) was the alternative; product call was meter fidelity.
NOTE_HOLD_CENTS_CEILING = 50.0


@dataclass(frozen=True)
class Note:
    name: str
    octave: int
    # Deviation from the note's exact pitch, in [-50, +50]. A freshly chosen
    # note lands in (-50, +50]; a name held through boundary wobble saturates
    # at the bounds (NOTE_HOLD_CENTS_CEILING) rather than reporting the true
    # distance, which is not something the meter can express.
    cents: float
    freq_hz: float  # the measured frequency this was derived from

    @property
    def label(self) -> str:
        return f"{self.name}{self.octave}"


def note_to_freq(name: str, octave: int, a4_hz: float = 440.0) -> float:
    midi = NOTE_NAMES.index(name) + 12 * (octave + 1)
    return a4_hz * 2.0 ** ((midi - A4_MIDI) / 12.0)


def note_midi(name: str, octave: int) -> int:
    return NOTE_NAMES.index(name) + 12 * (octave + 1)


def _midi_float(freq_hz: float, a4_hz: float) -> float:
    if freq_hz <= 0:
        raise ValueError(f"frequency must be positive, got {freq_hz}")
    return A4_MIDI + 12.0 * math.log2(freq_hz / a4_hz)


def freq_to_note(freq_hz: float, a4_hz: float = 440.0) -> Note:
    """Nearest note plus cent deviation (stateless)."""
    midi_float = _midi_float(freq_hz, a4_hz)
    midi = round(midi_float)
    name = NOTE_NAMES[midi % 12]
    return Note(
        name=name,
        octave=midi // 12 - 1,
        cents=(midi_float - midi) * 100.0,
        freq_hz=freq_hz,
    )


class NoteLatch:
    """Chooses which note name to display, with hysteresis and dwell.

    A pitch parked near a semitone boundary — which is exactly the case when
    an instrument is badly out of tune, i.e. while you are tuning it — would
    otherwise flip names on every vibrato cycle. Two guards:

    - hysteresis: the pitch must pass NOTE_HYSTERESIS_CENTS beyond the
      boundary at all before a change is even considered,
    - dwell: it must stay out there for dwell_frames consecutive readings,
      so a vibrato peak that pokes past the boundary is not a note change.

    While holding, reported cents saturate at +-NOTE_HOLD_CENTS_CEILING (the
    meter's own range, where the needle is already pinned), which reads as
    "sharp/flat past the end of the scale". The true distance to the held
    name is deliberately not reported: the pitch may have moved anywhere,
    and a leap used to surface as e.g. +2400c on the note we left.
    """

    def __init__(
        self,
        hysteresis_cents: float = NOTE_HYSTERESIS_CENTS,
        dwell_frames: int = NOTE_DWELL_FRAMES,
        ceiling_cents: float = NOTE_HOLD_CENTS_CEILING,
    ):
        self._hysteresis = hysteresis_cents
        self._dwell = dwell_frames
        # deliberately independent of the hysteresis: one decides *whether*
        # we are past the boundary, the other only how far we admit to being
        self._ceiling = ceiling_cents
        self._note: Note | None = None
        self._beyond = 0

    def reset(self) -> None:
        self._note = None
        self._beyond = 0

    def update(self, freq_hz: float, a4_hz: float = 440.0) -> Note:
        if not NOTE_LATCH_ENABLED:
            return freq_to_note(freq_hz, a4_hz)
        midi_float = _midi_float(freq_hz, a4_hz)
        held = self._note
        if held is not None:
            cents = (midi_float - note_midi(held.name, held.octave)) * 100.0
            if abs(cents) <= 50.0 + self._hysteresis:
                self._beyond = 0
            else:
                self._beyond += 1
            if self._beyond < self._dwell:
                # Report "off the scale", not the raw distance: while the name
                # is held the pitch may be anywhere, and the true deviation
                # from *that* name is not something the meter can show.
                return Note(
                    held.name,
                    held.octave,
                    math.copysign(min(abs(cents), self._ceiling), cents),
                    freq_hz,
                )

        self._note = freq_to_note(freq_hz, a4_hz)
        self._beyond = 0
        return self._note
