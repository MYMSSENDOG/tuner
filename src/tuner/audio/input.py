"""Audio input abstraction.

PortAudio (sounddevice) covers both Windows and macOS with one implementation,
so SoundDeviceInput is expected to stay the only implementation of this
Protocol. The seam exists so an OS-specific backend could be swapped in
without touching the engine or UI if that ever becomes necessary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

BlockCallback = Callable[[np.ndarray], None]  # mono float64 block


@dataclass(frozen=True)
class InputDevice:
    id: int
    name: str
    is_default: bool


class AudioInput(Protocol):
    def list_devices(self) -> list[InputDevice]: ...

    def start(self, device_id: int | None, callback: BlockCallback) -> int:
        """Open a stream on the device (None = system default) and begin
        delivering blocks to callback (called on the audio thread).
        Returns the stream's sample rate."""
        ...

    def stop(self) -> None: ...
