"""Real-display probe: does WindowStaysOnTopHint actually win the stacking?

Run as a subprocess by test_always_on_top.py so it can use the real Qt
platform while the main pytest process stays offscreen. Exit codes:
0 = verified, 77 = no real display (skip), 1 = stacking broken.
"""

import sys

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from tuner.app.main_window import MainWindow


class NullAudioInput:
    # intentionally not tests.fakes.FakeAudioInput: this script runs as a
    # bare subprocess where the tests package is not on sys.path
    def list_devices(self):
        return []

    def start(self, device_id, callback):
        return 44100

    def stop(self):
        pass


def top_window_at(point):
    return QApplication.topLevelAt(point)


def main() -> int:
    app = QApplication(sys.argv)
    if app.platformName() in ("offscreen", "minimal"):
        print("SKIP: no real display platform")
        return 77

    window = MainWindow(NullAudioInput())
    window.setGeometry(300, 300, 420, 420)
    window.show()
    window._pin_check.setChecked(True)

    rival = QWidget()
    rival.setWindowTitle("rival")
    rival.setGeometry(250, 250, 520, 520)  # fully covers the tuner window
    rival.show()
    rival.raise_()
    rival.activateWindow()
    QTest.qWait(800)

    center = window.frameGeometry().center()
    pinned_top = top_window_at(center)

    # negative control: unpin, raise the rival again — it must now cover us
    window._pin_check.setChecked(False)
    rival.raise_()
    rival.activateWindow()
    QTest.qWait(800)
    unpinned_top = top_window_at(center)

    if pinned_top is window and unpinned_top is rival:
        print("OK: pinned window stayed above, unpinned window was covered")
        return 0
    print(f"FAIL: pinned_top={pinned_top!r}, unpinned_top={unpinned_top!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
