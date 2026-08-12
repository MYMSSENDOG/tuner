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

from tuner.tools.playback import FilePlaybackInput


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
