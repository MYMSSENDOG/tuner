"""Pitch-detector implementations behind one interface.

The app runs YinDetector, full stop — the choice was never the user's to make.
Measured on the same signals, Spectral buys 0.005 -> 0.003 cents of precision
(a fiftieth of what the meter can show) and pays 52 -> 104ms of response at an
octave leap, past this project's own 100ms bar. So the engine takes a detector
at construction and nothing in the UI changes it.

The interface earns its keep elsewhere: tools/trace.py wraps whichever detector
in a recorder to keep every raw result, tests inject fakes, and SpectralDetector
stays reachable from `trace --detector spectral` as the second opinion when a
display oddity might be YIN's fault.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from tuner.core import pitch
from tuner.core.pitch import PitchResult
from tuner.core.spectral import estimate_f0, restore_weak_fundamental

# Below this input level a frame is "not being played at" — bow-stroke tails
# and room rumble still carry trackable pitch, and without a gate the display
# flips wildly between them (measured on the violin scale recording: 125
# note changes for 5 actual notes; -40dBFS yields exactly 5, -35 starts
# eating real notes).
#
# The default, not the value: this used to be a constant and the product rule
# was that no UI would expose it. A room decides otherwise — a 36s field
# recording (app/engine.py) had a tonal ~123Hz hum sitting just above this,
# so the gate was open on nothing but the room. One studio-measured number
# cannot fit every room, so it became adjustable from the level bar and this
# is where it starts.
INPUT_GATE_RMS = 10.0 ** (-40.0 / 20.0)


def _gated(frame: np.ndarray, gate_rms: float) -> bool:
    return float(np.sqrt(np.mean(frame * frame))) < gate_rms


class PitchDetector(Protocol):
    name: str
    frame_size: int  # samples of context each detection needs
    hop_size: int  # samples between detections; must exceed one detection's compute time
    center_offset: int  # samples back from frame end that a reading describes
    # Read on the audio thread, written from the UI when the user drags the
    # level bar. A float assignment is atomic under the GIL, so the worst a
    # race can do is apply the new gate one frame late.
    input_gate_rms: float

    def detect(self, frame: np.ndarray, sr: int) -> PitchResult: ...


class YinDetector:
    """The app's detector: fastest response, proven noise robustness.

    Two windows over the same buffer. The short one (2048 = 46ms) does the
    work and sets the response time. It cannot see below ~60Hz, though: YIN
    needs the period to fit in half the window, so 2048 samples bottom out
    around 43Hz and are unreliable well before that. Notes that low are real
    (double bass E1 = 41Hz, its lowest string), so when the short window
    reports a low or absent pitch, the full 4096-sample buffer is analysed
    too — the low register trades latency it can afford for a correct octave.
    """

    name = "YIN (fast)"
    frame_size = 2 * pitch.DEFAULT_FRAME_SIZE
    hop_size = pitch.DEFAULT_HOP_SIZE
    center_offset = pitch.DEFAULT_FRAME_SIZE // 2  # the short window's centre
    LOW_HANDOVER_HZ = 90.0
    LOW_FMIN_HZ = 38.0

    def __init__(self, input_gate_rms: float = INPUT_GATE_RMS):
        self.input_gate_rms = input_gate_rms

    def detect(self, frame: np.ndarray, sr: int) -> PitchResult:
        # everything except the low-register fallback looks at the recent
        # window only: level gate, pitch and the spectral cross-check must
        # all describe the same span of sound
        recent = frame[-pitch.DEFAULT_FRAME_SIZE :]
        if _gated(recent, self.input_gate_rms):
            return PitchResult(None, 0.0)
        short = pitch.detect(recent, sr)
        result, window = short, recent
        if short.freq_hz is None or short.freq_hz < self.LOW_HANDOVER_HZ:
            long = pitch.detect(frame, sr, fmin=self.LOW_FMIN_HZ)
            # the long window exists only to reach below the short one's
            # floor; letting it override anything else would trade the fast
            # window's noise robustness for nothing
            if (
                long.freq_hz is not None
                and long.freq_hz < self.LOW_HANDOVER_HZ
                and long.confidence >= short.confidence
            ):
                result, window = long, frame
        if result.freq_hz is None:
            return result
        # lag-domain YIN cannot tell T from T/k when the fundamental is a
        # few percent of the energy (oboe, low brass); one spectral
        # cross-check restores it
        freq = restore_weak_fundamental(window, sr, result.freq_hz, fmin=self.LOW_FMIN_HZ)
        if freq == result.freq_hz:
            return result
        return PitchResult(freq_hz=freq, confidence=result.confidence)


class SpectralDetector:
    """The reference annotator's estimator at real-time settings.

    Not what the app runs — a dev-tools second opinion (`trace --detector
    spectral`). More precise on stable pitch, but it needs a 4096-sample
    trailing window and hops four times more slowly, so it reacts at 81-104ms
    against YIN's 46-57ms (measured, step change of 1 to 12 semitones).
    """

    name = "Spectral (precise)"
    frame_size = 4096
    center_offset = 2048
    hop_size = 1024  # heavier per detection; ~23ms budget at 44.1kHz

    def __init__(self, input_gate_rms: float = INPUT_GATE_RMS):
        self.input_gate_rms = input_gate_rms

    def detect(self, frame: np.ndarray, sr: int) -> PitchResult:
        if _gated(frame, self.input_gate_rms):
            return PitchResult(None, 0.0)
        freq, confidence = estimate_f0(frame, sr, dtft_rounds=3)
        return PitchResult(freq_hz=freq, confidence=confidence)


# Every implementation that must satisfy the contract in tests/dsp/
# test_detector.py. Also how tools/promote.py replays a field report with the
# detector that was actually live when it was captured.
DETECTORS: tuple[type[PitchDetector], ...] = (YinDetector, SpectralDetector)
