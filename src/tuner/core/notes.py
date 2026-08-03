"""Hz <-> (note name, octave, cents) conversion, parameterized by A4 reference."""

from __future__ import annotations

import math
from dataclasses import dataclass

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

A4_MIDI = 69


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


def freq_to_note(freq_hz: float, a4_hz: float = 440.0) -> Note:
    if freq_hz <= 0:
        raise ValueError(f"frequency must be positive, got {freq_hz}")
    midi_float = A4_MIDI + 12.0 * math.log2(freq_hz / a4_hz)
    midi = round(midi_float)
    cents = (midi_float - midi) * 100.0
    name = NOTE_NAMES[midi % 12]
    octave = midi // 12 - 1
    return Note(name=name, octave=octave, cents=cents, freq_hz=freq_hz)
