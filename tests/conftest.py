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


def pytest_collection_modifyitems(config, items):
    """Timing tests measure per-call speed against a real-time budget, so on
    an xdist worker they measure CPU contention with the other workers
    instead — locally that turned a passing 8ms detection into 26ms.

    Same convention as the e2e tests: a test whose environment cannot support
    it skips itself and says how to get it run.
    """
    if getattr(config, "workerinput", None) is None:
        return  # serial run: measure away
    skip = pytest.mark.skip(reason="timing test — run serially: pytest -m perf -n0")
    for item in items:
        if "perf" in item.keywords:
            item.add_marker(skip)
