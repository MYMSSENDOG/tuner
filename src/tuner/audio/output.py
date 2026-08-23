"""Audio output abstraction — the mirror of input.py, for the metronome.

Same reasoning as the input side: PortAudio covers Windows and macOS with one
implementation, so the seam is not there to host a second backend. It is
there so the metronome can be driven by tests and by the comparison tools
without a sound card, exactly as FakeAudioInput does for the tuner.

The callback is a pull, not a push: the device asks for `frames` samples and
the metronome renders them. That is what keeps the beat on the audio clock
instead of on a GUI timer, which is the whole design of core/metronome.py.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np

RenderCallback = Callable[[int], np.ndarray]  # frames -> mono float64 block


class AudioOutput(Protocol):
    def start(self, render: RenderCallback) -> int:
        """Open the default output device and begin pulling blocks from
        `render` (called on the audio thread). Returns the stream's sample
        rate, which is what the renderer must have been built for.

        Raises RuntimeError if there is no usable output device. Machines
        without one are ordinary (a headless CI box, a desktop with the audio
        service stopped), so this is a condition callers handle, not a crash.
        """
        ...

    def stop(self) -> None: ...

    @property
    def sample_rate(self) -> int:
        """The rate a renderer should be built for before start() is called.
        Known in advance because the device is queried, not opened."""
        ...

    @property
    def latency_s(self) -> float:
        """Seconds between rendering a block and hearing it.

        Not a detail: the tuner suppresses its own clicks by wall-clock time
        (core/interference.py), and on a shared-mode Windows device this is
        tens of milliseconds - larger than the click itself. Guessing it with
        a wider suppression window would freeze the display for longer than
        the interference lasts, so it is asked for rather than assumed.
        Zero before the stream is open, and for doubles that never play.
        """
        ...
