"""Shared test configuration.

Qt runs offscreen by default so the suite works headless; e2e tests that
need a real display run their GUI checks in a subprocess with the real
platform (see tests/e2e/).

Also assembles the run's metric record: one directory per run, one file per
process (see tests/metrics.py).
"""

import json
import os
from datetime import UTC, datetime

import pytest

from tests import metrics

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


def _new_run_id() -> str:
    """Sortable, and it names the code it measured."""
    from tuner.analysis.trace import code_revision

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + code_revision()


RUN_ENV = "TUNER_METRICS_RUN"


def pytest_configure(config):
    """One run id for the whole run, workers included.

    The controller configures first and xdist spawns its workers afterwards,
    so an environment variable reaches every worker process. (The obvious
    alternative, seeding workerinput from pytest_configure_node, silently did
    not arrive here: the workers went on generating their own ids and their
    measurements landed in a directory of their own, seconds apart from the
    controller's.)
    """
    run = os.environ.get(RUN_ENV)
    if not run:
        run = _new_run_id()
        os.environ[RUN_ENV] = run
    config._metrics_run = run


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    worker = getattr(config, "workerinput", None)
    metrics.SUITE.write(config._metrics_run, worker["workerid"] if worker else "main")
    if worker is not None:
        return  # the controller alone describes the run
    directory = metrics.runs_dir() / config._metrics_run
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(
        json.dumps(
            {
                "run": config._metrics_run,
                "rev": config._metrics_run.split("-", 1)[1],
                "utc": config._metrics_run.split("-", 1)[0],
                "args": config.invocation_params.args,
                "exit": int(exitstatus),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
