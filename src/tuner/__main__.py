import sys

from PySide6.QtWidgets import QApplication

from tuner.app.main_window import MainWindow, enable_ctrl_c
from tuner.audio.sounddevice_input import SoundDeviceInput


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(SoundDeviceInput())
    _sigint_timer = enable_ctrl_c(window)
    window.show()
    window.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
