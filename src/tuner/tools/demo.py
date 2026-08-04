"""Watch the tuner track an audio file you can hear.

    python -m tuner.tools.demo tests/fixtures/audio/violin_scale_G3B3.aiff
    python -m tuner.tools.demo some_recording.wav --loop

Plays the file to the speakers while feeding the tuner engine the very same
sample blocks — what you hear and what the needle tracks are sample-identical
(no microphone or room acoustics in the loop). At the end of the file the
tuner sees silence; --loop repeats forever (Ctrl+C or close the window).
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import sounddevice as sd
import soundfile as sf

from tuner.audio.input import BlockCallback, InputDevice

BLOCK_SIZE = 256


class FilePlaybackInput:
    """AudioInput that plays a file to the default output device and mirrors
    every block into the analysis callback."""

    def __init__(self, path: str, loop: bool = False):
        signal, sr = sf.read(path, always_2d=True)
        self._signal = np.ascontiguousarray(signal.mean(axis=1), dtype=np.float32)
        self._sr = sr
        self._loop = loop
        self._pos = 0
        self._stream: sd.OutputStream | None = None

    def list_devices(self) -> list[InputDevice]:
        return []  # source is the file; there is nothing to select

    def start(self, device_id: int | None, callback: BlockCallback) -> int:
        self.stop()

        def on_block(outdata: np.ndarray, frames: int, time, status) -> None:
            chunk = self._signal[self._pos : self._pos + frames]
            self._pos += frames
            if len(chunk) < frames:
                if self._loop and len(self._signal) > 0:
                    self._pos = frames - len(chunk)
                    chunk = np.concatenate([chunk, self._signal[: self._pos]])
                else:
                    chunk = np.concatenate(
                        [chunk, np.zeros(frames - len(chunk), dtype=np.float32)]
                    )
            outdata[:, 0] = chunk
            callback(chunk.astype(np.float64))

        self._stream = sd.OutputStream(
            samplerate=self._sr, channels=1, blocksize=BLOCK_SIZE, callback=on_block
        )
        self._stream.start()
        return self._sr

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio", help="audio file to play and track")
    parser.add_argument("--loop", action="store_true", help="repeat the file forever")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from tuner.app.main_window import MainWindow, enable_ctrl_c

    app = QApplication(sys.argv if argv is None else [sys.argv[0]])
    window = MainWindow(FilePlaybackInput(args.audio, loop=args.loop))
    window.setWindowTitle(f"Tuner — {args.audio}")
    _sigint_timer = enable_ctrl_c(window)
    window.show()
    window.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
