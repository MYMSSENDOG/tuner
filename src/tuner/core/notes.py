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


@dataclass(frozen=True)
class Note:
    name: str
    octave: int
    cents: float  # deviation from the note's exact pitch, in (-50, +50]
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

    While holding, reported cents simply exceed +-50 (the meter clamps the
    needle), which reads as "sharp/flat past the end of the scale".
    """

    def __init__(
        self,
        hysteresis_cents: float = NOTE_HYSTERESIS_CENTS,
        dwell_frames: int = NOTE_DWELL_FRAMES,
    ):
        self._hysteresis = hysteresis_cents
        self._dwell = dwell_frames
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
                return Note(held.name, held.octave, cents, freq_hz)

        self._note = freq_to_note(freq_hz, a4_hz)
        self._beyond = 0
        return self._note
