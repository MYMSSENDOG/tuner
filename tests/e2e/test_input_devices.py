"""Real-hardware audio input tests (Windows and macOS alike — PortAudio).

Skipped automatically when the machine has no input device (e.g. CI runners).
"""

import time

import pytest

sd = pytest.importorskip("sounddevice")

from tuner.audio.sounddevice_input import SoundDeviceInput  # noqa: E402


def _list_devices():
    try:
        return SoundDeviceInput().list_devices()
    except Exception:
        return []


DEVICES = _list_devices()

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not DEVICES, reason="no audio input device on this machine"),
]


def wait_for_blocks(blocks, minimum=5, timeout=3.0):
    deadline = time.monotonic() + timeout
    while len(blocks) < minimum and time.monotonic() < deadline:
        time.sleep(0.05)
    return len(blocks) >= minimum


def test_device_enumeration():
    assert all(d.name for d in DEVICES)
    assert sum(1 for d in DEVICES if d.is_default) <= 1
    assert len({d.id for d in DEVICES}) == len(DEVICES)


def test_capture_from_default_device():
    audio = SoundDeviceInput()
    blocks = []
    sr = audio.start(None, blocks.append)
    try:
        assert sr > 0
        assert wait_for_blocks(blocks), "no audio blocks arrived from default device"
        assert all(b.ndim == 1 and len(b) > 0 for b in blocks[:5])
    finally:
        audio.stop()


def test_hot_swap_across_all_devices():
    """Switch through every input device on one SoundDeviceInput instance,
    exactly as the UI does, and verify each actually delivers audio."""
    audio = SoundDeviceInput()
    try:
        for device in DEVICES:
            blocks = []
            sr = audio.start(device.id, blocks.append)
            assert sr > 0, device.name
            assert wait_for_blocks(blocks), f"no blocks from {device.name}"
    finally:
        audio.stop()


def test_stop_ends_delivery():
    audio = SoundDeviceInput()
    blocks = []
    audio.start(None, blocks.append)
    wait_for_blocks(blocks, minimum=1)
    audio.stop()
    count = len(blocks)
    time.sleep(0.3)
    assert len(blocks) == count
