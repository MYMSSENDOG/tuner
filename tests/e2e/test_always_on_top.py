"""True e2e for always-on-top: verifies actual window stacking on a real
display (Windows and macOS — the probe is pure Qt).

The Qt platform is fixed per process and the main suite runs offscreen, so
the check runs in a subprocess with the real platform.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

PROBE = Path(__file__).with_name("_stacking_probe.py")


@pytest.mark.e2e
def test_always_on_top_wins_stacking():
    env = os.environ.copy()
    env.pop("QT_QPA_PLATFORM", None)  # let Qt pick the real display platform
    result = subprocess.run(
        [sys.executable, str(PROBE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.strip())
    assert result.returncode == 0, result.stdout + result.stderr
