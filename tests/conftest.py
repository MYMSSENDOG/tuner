"""Shared test configuration.

Qt runs offscreen by default so the suite works headless; e2e tests that
need a real display run their GUI checks in a subprocess with the real
platform (see tests/e2e/).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
