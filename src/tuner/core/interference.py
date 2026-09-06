"""Sound the app makes itself, which the microphone hears back.

The metronome plays a click; the tuner's microphone picks it up; the detector
has no way to know it is not the instrument. Something has to tell it.

That "something" is deliberately an interface with one small implementation
today and a bigger one planned:

- **now** — `ScheduledClicks`. The app played the click, so it knows when. It
  says "do not look during this stretch" and the display holds instead of
  following a transient it should never have seen.
- **later** — a source that *listens*: finds the click pattern in the input
  itself, using what it already knows (the tempo, the click's own spectrum)
  as a prior, and subtracts or masks only what actually arrived. That is
  strictly better — it survives clock drift between the output and input
  devices, catches a metronome coming out of someone else's speaker, and
  costs nothing when the room is quiet. It is not built here.

The seam is what makes the second one a swap rather than a rewrite, and this
file is the whole seam: one method, answered in wall-clock seconds because
the two audio streams are separate devices with separate sample clocks and
the monotonic clock is the only axis they share.

=== display switch: set False to hear what the tuner does with the clicks ===
"""

from __future__ import annotations

import math
from collections import deque
from typing import Protocol

import numpy as np

CLICK_SUPPRESSION_ENABLED = True

# How wide a click's shadow is, either side of the moment it was audible.
# The device's own output latency is *not* in here - app/metronome.py asks the
# stream for it and shifts the click time instead, because padding for it
# would freeze the display for far longer than the click lasts.
#
# So the lead is only flight time and clock slop (a metre of air is 3ms), and
# the tail is the click's 20ms body plus what the room adds after it.
#
# Driver (tests/integration/test_metronome_interference.py, synthetic clicks
# over a violin note, name segments on screen / frames frozen):
#   tail 20ms  120 BPM: 2 seg, 14% | 208 BPM: 3 seg, 25%
#   tail 30ms  120 BPM: 2 seg, 15% | 208 BPM: 3 seg, 29%
#   tail 40ms  120 BPM: 2 seg, 17% | 208 BPM: 3 seg, 32%
# (suppression off: 8 and 17 segments). The knee is 20 - everything above it
# buys nothing here and costs frozen frames. 30 is taken anyway: a synthetic
# click has no room behind it, and one extra click-length is the cheapest
# insurance against a real one. Re-measure from a room capture (Ctrl+L, then
# tools/promote) before trusting 20.
CLICK_LEAD_S = 0.010
CLICK_TAIL_S = 0.030
# ...which generalises: the tail is the sound's own length plus what the room
# adds after it. The 20ms click gives 20 + 10 = 30, exactly the value the
# sweep settled on, and a 45ms beep gets the 55 it needs (at 30 its tail
# reached past the window and one stray name got through).
ROOM_DECAY_S = 0.010


class InterferenceSource(Protocol):
    """Knows when the app's own noise was audible to the microphone."""

    def observe(self, block: np.ndarray, t_end: float, sr: int) -> None:
        """Every input block, as it arrives, with the wall-clock time of its
        last sample. A source that works it out from the microphone needs
        this; one that already knows ignores it."""
        ...

    def contaminates(self, t_start: float, t_end: float) -> bool:
        """Was our own sound audible at any point in [t_start, t_end]?

        Both are monotonic-clock seconds, and the span is the stretch of audio
        one detection looked at — not the instant it finished.
        """
        ...


class ScheduledClicks:
    """Interference we can name because we scheduled it.

    Written from the output thread (one append per click) and read from the
    input thread (one scan per detection). Both operations are a single
    bytecode on a bounded deque, so neither side can see a half-written
    timeline and no lock is needed on the audio path.
    """

    def __init__(
        self,
        lead_s: float = CLICK_LEAD_S,
        tail_s: float = CLICK_TAIL_S,
        keep: int = 32,
    ):
        self._lead = lead_s
        self._tail = tail_s
        # a bounded ring: at 30 BPM the oldest of 32 is a minute old, and
        # nothing ever asks about a span that far back
        self._clicks: deque[float] = deque(maxlen=keep)

    def observe(self, block: np.ndarray, t_end: float, sr: int) -> None:
        """Nothing to learn: this source already knows what it played."""

    def clicked_at(self, t: float) -> None:
        """Record that a click was audible at monotonic time `t`."""
        self._clicks.append(t)

    def clear(self) -> None:
        self._clicks.clear()

    def contaminates(self, t_start: float, t_end: float) -> bool:
        if not CLICK_SUPPRESSION_ENABLED:
            return False
        return any(
            t_start <= click + self._tail and click - self._lead <= t_end
            for click in tuple(self._clicks)
        )


# --- finding the metronome in the input ---------------------------------
#
# The source above works because we played the click. This one works because
# we know the *tempo* and can go looking. It never asks when we played, which
# is what makes it survive the things the scheduled one cannot: an output
# device whose clock drifts against the input's, a latency figure the driver
# reports wrongly, or a metronome coming out of someone else's speaker.
#
# It also costs nothing when there is nothing to suppress. Wearing headphones
# means no click ever reaches the microphone; the scheduled source freezes the
# display 15-29% of the time anyway, and this one does not freeze it at all,
# because it never hears anything to lock onto.

# Finding the metronome's *phase* when the tempo is already known.
#
# Nominating individual onsets does not survive real audio: measured over six
# seconds of cello with clicks mixed in, a level-jump detector found 13 of 14
# clicks and 17 things that were not clicks. Any phase estimate that trusts
# one transient is then mostly tracking the instrument.
#
# So nothing is nominated. A continuous novelty (positive spectral flux, the
# standard onset measure) is folded onto the beat period and accumulated: the
# clicks land in the same bin every beat and add up, everything else is spread
# across the bar and does not. The peak is the phase, and how far it stands
# above the rest of the bar is the confidence.
NOVELTY_MIN_HZ = 400.0  # below this the instrument's own energy dominates
FOLD_HALF_LIFE_S = 3.0  # how long a beat keeps contributing to the histogram
# Peak vs. mean of the folded bar before anything is suppressed.
#
# Chosen to catch clicks, not to reject anything: swept over the whole fixture
# corpus (27 instrument x tempo combinations, 40/120/200 BPM), the weakest
# audible click still stood 2.7x above the bar's mean (cello A3 at 200 BPM,
# where the beats are close together and each carries less). 2.0 keeps a
# margin under that, and a held note with the click turned almost off sits at
# 1.3, so the gap is real in both directions.
#
# It cannot be set from the other side, and this is worth being plain about:
# with the tempo as the only prior there is no threshold that separates "our
# click" from "the player attacking on the beat". The same sweep with no
# clicks at all reaches 86 on the scale recordings, and no novelty band and no
# peak-sharpness measure told the two apart. That is information, not code:
# both are a transient at the beat.
#
# Which is survivable because the alternative has the same blind spot. The
# scheduled source freezes the beat window whatever is in it, including the
# player's own on-beat attack. Locking onto the player is therefore no worse
# than not listening at all, while listening is strictly better whenever the
# click is inaudible (headphones: 0% of frames frozen against 8-29%).
LOCK_PEAK_RATIO = 3.0
LOCK_MIN_BEATS = 2.0  # and this much of the bar actually observed, which is
# what a lock costs: the first two clicks of a session reach the display
# (3.0s at 40 BPM, 0.6s at 200). Measured phase error after that is 5.5ms,
# one block, which is what CLICK_LEAD_S is for.


def _positive_flux(magnitude: np.ndarray, previous: np.ndarray | None) -> float:
    """How much spectral energy *appeared* since the last block."""
    if previous is None or previous.shape != magnitude.shape:
        return 0.0
    return float(np.sum(np.maximum(magnitude - previous, 0.0)))


class HeardClicks:
    """Interference we find, given only how often to expect it.

    Never asks when we played, which is what lets it survive the things the
    scheduled source cannot: an output clock drifting against the input's, a
    latency figure the driver reports wrongly, a metronome coming out of
    someone else's speaker. And it suppresses nothing when nothing is heard —
    on headphones the scheduled source still freezes the display 5-29% of the
    time (docs/metronome.md) for a click no microphone ever received.
    """

    def __init__(
        self,
        lead_s: float = CLICK_LEAD_S,
        tail_s: float = CLICK_TAIL_S,
        peak_ratio: float = LOCK_PEAK_RATIO,
    ):
        self._lead = lead_s
        self._tail = tail_s
        self._peak_ratio = peak_ratio
        self._period: float | None = None
        self._previous: np.ndarray | None = None
        self._window: np.ndarray | None = None
        self._bins: np.ndarray | None = None
        self._observed_s = 0.0

    # --- what the metronome tells it (the whole of the prior) ---

    def set_period(self, period_s: float) -> None:
        """The tempo, as seconds per beat. Changing it clears what was folded:
        those counts were binned against the old bar and mean nothing now."""
        if self._period is not None and abs(self._period - period_s) < 1e-9:
            return
        self._period = period_s
        self._forget()

    def set_sound_length(self, seconds: float) -> None:
        """How long the sound we are making lasts. The window has to cover it
        or its tail reaches past and gets tuned."""
        self._tail = seconds + ROOM_DECAY_S

    def idle(self) -> None:
        """The metronome stopped. Nothing to look for."""
        self._period = None
        self._forget()

    def _forget(self) -> None:
        self._bins = None
        self._previous = None
        self._observed_s = 0.0

    # --- what the microphone tells it ---

    def observe(self, block: np.ndarray, t_end: float, sr: int) -> None:
        period = self._period
        if period is None or len(block) == 0:
            return
        # windowed, and it matters more than it looks. A block boundary falls
        # at a different point in the waveform every time (440Hz is 100.2
        # samples, the block is 256), so an unwindowed FFT of a perfectly
        # steady tone changes block to block purely from leakage. That is
        # spurious novelty spread evenly across the bar, and it drowns the
        # thing being looked for: measured on a held violin note with clicks
        # over it, peak/mean was 1.7 without this and 40+ with it.
        if self._window is None or len(self._window) != len(block):
            self._window = np.hanning(len(block))
        spectrum = np.abs(np.fft.rfft(block * self._window))
        spectrum[: int(NOVELTY_MIN_HZ * len(block) / sr)] = 0.0
        novelty = _positive_flux(spectrum, self._previous)
        self._previous = spectrum

        bin_s = len(block) / sr
        if self._bins is None:
            # the bar is divided once, from whatever the device's block size
            # is. Not re-divided when a block arrives short (the last one of a
            # stream always does), which used to throw the whole histogram
            # away at the very moment it was most sure.
            self._bins = np.zeros(max(round(period / bin_s), 4))
            self._observed_s = 0.0
        # the flux describes the block that just ended, so bin it by where
        # that block *started* in the bar
        t_start = t_end - bin_s
        index = int((t_start % period) / period * len(self._bins)) % len(self._bins)
        self._bins *= 0.5 ** (bin_s / FOLD_HALF_LIFE_S)
        self._bins[index] += novelty
        self._observed_s += bin_s

    # --- the phase, and what the tuner asks ---

    @property
    def locked(self) -> bool:
        return self._peak() is not None

    def _peak(self) -> float | None:
        """Where in the bar the clicks are, in seconds, or None if unconvinced."""
        bins, period = self._bins, self._period
        if bins is None or period is None:
            return None
        if self._observed_s < LOCK_MIN_BEATS * period:
            return None
        mean = float(bins.mean())
        peak = int(np.argmax(bins))
        if mean <= 0.0 or bins[peak] < self._peak_ratio * mean:
            return None
        # sub-bin position: the click is somewhere inside its 5.8ms bin, and
        # its energy leaks into the neighbours in proportion
        before, at, after = bins[peak - 1], bins[peak], bins[(peak + 1) % len(bins)]
        denominator = before - 2.0 * at + after
        offset = 0.5 * (before - after) / denominator if denominator != 0 else 0.0
        return (peak + min(max(offset, -0.5), 0.5)) * period / len(bins)

    def contaminates(self, t_start: float, t_end: float) -> bool:
        if not CLICK_SUPPRESSION_ENABLED:
            return False
        phase, period = self._peak(), self._period
        if phase is None or period is None:
            return False
        lo, hi = t_start - self._tail, t_end + self._lead
        k = math.ceil((lo - phase) / period)
        return phase + k * period <= hi
