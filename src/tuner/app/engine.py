"""Assembles audio input -> pitch detection -> tracking -> TunerReading.

Plain Python (no Qt) so the whole pipeline is testable with a fake AudioInput.
Readings are emitted on the audio thread; the UI bridges them to its own
thread.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from tuner.audio.input import AudioInput
from tuner.core.detector import PitchDetector, YinDetector
from tuner.core.interference import InterferenceSource
from tuner.core.notes import Note, NoteLatch
from tuner.core.tracker import PitchTracker, State

if TYPE_CHECKING:  # the capture imports this module, so keep the edge one-way
    from tuner.app.capture import FieldCapture

DIGITAL_SILENCE_DBFS = -120.0  # exact zeros: no OS permission / dead device

# === display switch: set False to make the note latch ride through attacks ===
# A note starting is not a detection glitch, and the difference is audible in
# the level: an attack lifts the input well above whatever was there before,
# while an octave glitch under interference happens at a steady level. The
# latch's dwell exists for the second case (docs/note-latch-tuning.md), so
# when the first one is what happened, it is let go of instead.
#
# Driver: a 36s field recording into a room mic whose noise floor was a tonal
# ~123Hz hum sitting just above the input gate. The latch therefore always
# held a name, and every attack spent the whole dwell showing that name
# pinned at the end of the scale - detector right after 20ms (median),
# display after 81ms.
ATTACK_RELEASE_ENABLED = True
ONSET_RISE_DB = 12.0  # how far above the floor counts as a new note
# One block is 5.8ms - less than a period at the hum frequencies this has to
# tell apart, so its RMS swings several dB on a steady tone and any threshold
# fires constantly. The comparison is between two smoothed levels instead:
# one that follows the input within ~20ms, and a floor that drops at once and
# creeps back up (~1.5s to close a 12dB gap), so a held note cannot drag the
# floor up behind it and re-trigger.
LEVEL_SMOOTHING = 0.25
FLOOR_RISE_PER_BLOCK = 0.02


@dataclass(frozen=True)
class TunerReading:
    state: State
    note: Note | None  # present iff state is OK
    # input level of the newest block; real microphones always carry a noise
    # floor, so a sustained DIGITAL_SILENCE_DBFS means "no signal at all"
    level_dbfs: float = DIGITAL_SILENCE_DBFS


class TunerEngine:
    def __init__(
        self,
        audio_input: AudioInput,
        on_reading: Callable[[TunerReading], None],
        detector: PitchDetector | None = None,
        tracker_factory: Callable[[float], PitchTracker] | None = None,
        capture: FieldCapture | None = None,
        interference: InterferenceSource | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._audio = audio_input
        self._on_reading = on_reading
        # optional rolling recorder (app/capture.py); None costs nothing
        self._capture = capture
        # optional: something that knows when the app's own sound (the
        # metronome) was audible to the microphone (core/interference.py)
        self._interference = interference
        self._clock = clock
        self._a4_hz = 440.0
        self._sr = 0
        self._detector: PitchDetector = detector or YinDetector()
        # (dt_s) -> PitchTracker; injectable so variant-comparison harnesses
        # can run several display policies over the same audio
        self._tracker_factory = tracker_factory or (lambda dt: PitchTracker(dt_s=dt))
        self._reset_pipeline()

    @property
    def a4_hz(self) -> float:
        return self._a4_hz

    def set_a4(self, a4_hz: float) -> None:
        self._a4_hz = a4_hz

    @property
    def detector_name(self) -> str:
        """What a field report should record as having produced its readings."""
        return self._detector.name

    @property
    def input_gate_dbfs(self) -> float:
        return 20.0 * math.log10(self._detector.input_gate_rms)

    def set_input_gate_dbfs(self, level_dbfs: float) -> None:
        """Below this input level the detector does not judge.

        Live: written from the GUI thread while the audio thread reads it
        (core/detector.py). No restart, because a gate the user is dragging
        has to be heard while they drag it.
        """
        self._detector.input_gate_rms = 10.0 ** (level_dbfs / 20.0)

    def start(self, device_id: int | None = None) -> None:
        self._reset_pipeline()
        self._sr = self._audio.start(device_id, self._on_block)

    def stop(self) -> None:
        self._audio.stop()

    def _reset_pipeline(self) -> None:
        self._latch = NoteLatch()
        self._level_dbfs = DIGITAL_SILENCE_DBFS
        # None until the first block: a floor guessed before any audio would
        # make the whole start of the stream look like one long attack
        self._level_fast: float | None = None
        self._floor_dbfs: float | None = None
        # dt drives the smoother; sample rate is only known after start(),
        # but a wrong-by-10% dt (e.g. 48kHz devices) is immaterial to it
        self._tracker = self._tracker_factory(self._detector.hop_size / (self._sr or 44100))
        self._buffer = np.zeros(self._detector.frame_size)
        self._filled = 0
        self._pending = 0
        self._last_reading = TunerReading(State.SILENT, None)

    def _track_floor(self, level_dbfs: float) -> bool:
        """Follow the quiet level; report whether this block is an attack."""
        if self._floor_dbfs is None or self._level_fast is None:
            self._level_fast = self._floor_dbfs = level_dbfs
            return False
        self._level_fast += (level_dbfs - self._level_fast) * LEVEL_SMOOTHING
        onset = self._level_fast > self._floor_dbfs + ONSET_RISE_DB
        if self._level_fast < self._floor_dbfs:
            self._floor_dbfs = self._level_fast
        else:
            self._floor_dbfs += (self._level_fast - self._floor_dbfs) * FLOOR_RISE_PER_BLOCK
        return onset

    def _on_block(self, block: np.ndarray) -> None:
        if self._capture is not None:
            self._capture.push_block(block, self._sr)
        rms = float(np.sqrt(np.mean(block * block)))
        self._level_dbfs = 20.0 * math.log10(rms) if rms > 0.0 else DIGITAL_SILENCE_DBFS
        onset = self._track_floor(self._level_dbfs)
        if self._interference is not None:
            # every block, not just the ones a detection lands on: a source
            # that finds the metronome in the input needs the whole stream
            self._interference.observe(block, self._clock(), self._sr or 44100)
        n = len(block)
        frame_size = self._detector.frame_size
        self._buffer = np.roll(self._buffer, -n)
        self._buffer[-n:] = block
        self._filled = min(self._filled + n, frame_size)
        self._pending += n
        if self._filled < frame_size or self._pending < self._detector.hop_size:
            return
        self._pending = 0
        if self._contaminated():
            # our own metronome is in this frame. Hold the display rather than
            # blank it: nothing about the instrument changed, we just stopped
            # looking for a moment, and a reading that vanishes every beat is
            # a worse lie than one that is briefly stale.
            self._emit(self._last_reading, raw_hz=None, confidence=0.0)
            return
        raw = self._detector.detect(self._buffer, self._sr)
        tracked = self._tracker.update(raw)
        if onset and ATTACK_RELEASE_ENABLED:
            # a new note, not a glitch: the name may follow it after the short
            # attack dwell instead of the one that exists to absorb glitches
            # arriving at a steady level
            self._latch.attack()
        if tracked.freq_hz:
            note = self._latch.update(tracked.freq_hz, self._a4_hz)
        else:
            note = None
            self._latch.reset()
        self._emit(
            TunerReading(state=tracked.state, note=note, level_dbfs=self._level_dbfs),
            raw_hz=raw.freq_hz,
            confidence=raw.confidence,
        )

    def _contaminated(self) -> bool:
        """Was the app's own sound inside the span this detection looked at?

        The span is 2*center_offset, not frame_size: a detector declares
        center_offset as how far back from the frame end its reading actually
        describes, so that is the window the pitch came from. YIN carries a
        4096-sample buffer but reads pitch off the most recent 2048 (the rest
        is the low-register fallback), and using the buffer instead of the
        window doubles what a click costs - 27% of frames frozen at 120 BPM
        against 15% (docs/metronome.md).
        """
        if self._interference is None:
            return False
        now = self._clock()
        span = 2 * self._detector.center_offset / (self._sr or 44100)
        return self._interference.contaminates(now - span, now)

    def _emit(self, reading: TunerReading, raw_hz: float | None, confidence: float) -> None:
        reading = TunerReading(reading.state, reading.note, self._level_dbfs)
        self._last_reading = reading
        if self._capture is not None:
            self._capture.push_reading(reading, raw_hz, confidence)
        self._on_reading(reading)
