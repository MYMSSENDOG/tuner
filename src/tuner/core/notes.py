"""Hz <-> (note name, octave, cents) conversion, parameterized by A4 reference."""

from __future__ import annotations

import math
from dataclasses import dataclass

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

A4_MIDI = 69

# Whether the displayed note name is chosen by policy at all. With this off
# every frame shows the raw nearest note, which is what a detection glitch
# repaints the whole meter with.
# === display switch: set False for the raw nearest note every frame ===
NOTE_LATCH_ENABLED = True

NOTE_DWELL_FRAMES = 12  # ~70ms at the default hop; measured against vibrato

# Ceiling on the deviation a *held* note may report. While holding, cents are
# measured from the name on screen, so a pitch that moved somewhere else
# entirely produced absurd readings (a two-octave leap read +2400c on the note
# we left, and octave glitches under interference read past 1200c). The hold
# itself has to stay - it is what stops the name flickering when detection
# oscillates - so only the number is bounded.
# Set to the meter's own range: the badge must never claim a deviation the
# scale cannot show. Past this the pitch is closer to the neighbouring note,
# and that is what gets displayed - see NOTE_RELEASE_LOW_CENTS - so in normal
# use the clamp is unreachable. It survives for one case only: a departure
# past the release window (an octave-scale detection glitch) still waits out
# the dwell, and its number is pinned here meanwhile.
# === product rule: the number never leaves +-50. Read the meter, not the
# === latch: at +51c the instrument is 49c flat of the next note, and that is
# === what a tuner should say. Trying 65c instead was rejected on sight -
# === docs/note-latch-tuning.md.
NOTE_HOLD_CENTS_CEILING = 50.0

# Past the meter's range the pitch belongs to the neighbouring note, so the
# name moves there at once rather than sitting at the end of the scale waiting
# out the dwell. This is the product rule above, expressed as the point where
# the hold lets go.
# The upper bound is what keeps the dwell in charge of the case it was proven
# on: under interference the detector jumps an octave or more (+-560 to 2521c
# measured), which is a glitch and not a note change. Dropping the bound costs
# 3 more fixtures their flicker seal (89 -> 105 segments).
# === driver: docs/note-latch-tuning.md "즉시 재표기 창"
NOTE_RELEASE_LOW_CENTS = 50.0
NOTE_RELEASE_HIGH_CENTS = 250.0


@dataclass(frozen=True)
class Note:
    name: str
    octave: int
    # Deviation from the note's exact pitch. A freshly chosen note lands in
    # (-50, +50]; a name held through boundary wobble reads out to the edge of
    # that wobble band and saturates there (NOTE_HOLD_CENTS_CEILING) rather
    # than reporting the true distance, which the meter cannot express. The
    # needle stops at +-50 regardless (meter_model.needle_angle_deg).
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
    """Chooses which note name to display: the nearest one, except across a
    detection glitch.

    Departures from the held note fall into two measured populations, and the
    distance alone tells them apart:

    - within the release window (NOTE_RELEASE_LOW_CENTS..HIGH) the pitch has
      simply moved to a neighbouring note, so the name follows immediately and
      the number restarts from that note's side. Holding on would only pin the
      old name at the end of the scale.
    - past the window it is an octave-scale jump (+-560..2521c measured under
      interference), which is the detector glitching rather than the player
      changing note. Those must survive `dwell_frames` consecutive readings
      before the name moves, and the cents reported meanwhile saturate at
      +-NOTE_HOLD_CENTS_CEILING — the true distance is deliberately not
      reported, as a leap used to surface as e.g. +2400c on the note we left.

    So the dwell no longer guards semitone boundaries, only glitch distances.
    The cost of that is real and measured: a vibrato straddling a boundary now
    repaints the name on every cycle (flute_vib_C6: 3 -> 15 segments). The
    +-50 product rule wins anyway — see docs/note-latch-tuning.md.
    """

    def __init__(
        self,
        dwell_frames: int = NOTE_DWELL_FRAMES,
        ceiling_cents: float = NOTE_HOLD_CENTS_CEILING,
        release_low_cents: float = NOTE_RELEASE_LOW_CENTS,
        release_high_cents: float = NOTE_RELEASE_HIGH_CENTS,
    ):
        self._dwell = dwell_frames
        self._release_low = release_low_cents
        self._release_high = release_high_cents
        # independent of the window: that decides *whether* the name moves,
        # this only bounds how far off we admit to being while it does not
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
            if self._release_low <= abs(cents) <= self._release_high:
                # Past the meter's range: the pitch is nearer this note's
                # neighbour, so show that instead of claiming the old name is
                # off the scale. Only glitches land past the window.
                self._note = freq_to_note(freq_hz, a4_hz)
                self._beyond = 0
                return self._note
            # only two cases reach here, the window having returned already:
            # inside the meter's range (nothing to decide) or past the window
            # (a glitch, which has to prove itself for the whole dwell)
            if abs(cents) < self._release_low:
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
