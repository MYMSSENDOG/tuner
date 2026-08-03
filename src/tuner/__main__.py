import sys

from PySide6.QtWidgets import QApplication

from tuner.app.main_window import MainWindow
from tuner.audio.sounddevice_input import SoundDeviceInput


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(SoundDeviceInput())
    window.show()
    window.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
