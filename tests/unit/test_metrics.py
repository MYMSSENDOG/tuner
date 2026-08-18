"""The measurement record and the scoreboard that reads it.

These use their own Recorder and their own directory: the suite's live
recorder is filling up while they run, and a test must not touch it.
"""

from __future__ import annotations

import json

import pytest

from tests.metrics import Recorder
from tuner.tools.scoreboard import (
    Change,
    compare,
    main,
    read_runs,
    regressions,
    table,
)


def write_run(base, run_id: str, values: dict[str, float], part: str = "main", **kwargs):
    recorder = Recorder()
    for name, value in values.items():
        recorder.record(name, value, better=kwargs.get(name, "lower"))
    recorder.write(run_id, part, base=base)
    return recorder


def test_record_returns_the_value_and_keeps_direction():
    recorder = Recorder()
    assert recorder.record("a/b", 1.5) == 1.5
    recorder.record("c/d", 0.9, unit="ratio", better="higher")
    assert [(m.name, m.value, m.better) for m in recorder.taken] == [
        ("a/b", 1.5, "lower"),
        ("c/d", 0.9, "higher"),
    ]
    with pytest.raises(ValueError):
        recorder.record("e/f", 1.0, better="smaller")


def test_nothing_measured_writes_nothing(tmp_path):
    assert Recorder().write("20260101T000000Z-abc", "main", base=tmp_path) is None
    assert read_runs(tmp_path) == []


def test_runs_are_read_oldest_first_with_workers_merged(tmp_path):
    write_run(tmp_path, "20260101T000000Z-aaaaaaa", {"x/one": 1.0})
    write_run(tmp_path, "20260102T000000Z-bbbbbbb", {"x/one": 2.0}, part="gw0")
    write_run(tmp_path, "20260102T000000Z-bbbbbbb", {"x/two": 3.0}, part="gw1")

    runs = read_runs(tmp_path)
    assert [r.rev for r in runs] == ["aaaaaaa", "bbbbbbb"]
    # an xdist run is spread over one file per worker and must read as one run
    assert runs[1].values == {"x/one": 2.0, "x/two": 3.0}


def test_written_records_are_readable_json(tmp_path):
    write_run(tmp_path, "20260101T000000Z-aaaaaaa", {"x/one": 1.25})
    line = (tmp_path / "runs" / "20260101T000000Z-aaaaaaa" / "main.jsonl").read_text(
        encoding="utf-8"
    )
    assert json.loads(line) == {
        "name": "x/one",
        "value": 1.25,
        "unit": "",
        "better": "lower",
    }


def test_direction_decides_what_counts_as_worse():
    assert Change("jitter", 1.0, 2.0, "lower").worse
    assert not Change("jitter", 2.0, 1.0, "lower").worse
    assert Change("vibrato", 0.9, 0.8, "higher").worse
    assert not Change("vibrato", 0.8, 0.9, "higher").worse
    assert Change("jitter", 1.0, 1.5, "lower").pct == 50.0
    assert Change("flashes", 0.0, 1.0, "lower").pct is None  # nothing to divide by


def test_compare_ranks_regressions_first(tmp_path):
    write_run(tmp_path, "20260101T000000Z-aaaaaaa", {"a/x": 1.0, "b/y": 1.0, "c/z": 1.0})
    write_run(
        tmp_path,
        "20260102T000000Z-bbbbbbb",
        {"a/x": 0.5, "b/y": 1.1, "c/z": 2.0},  # better, slightly worse, much worse
    )
    before, after = read_runs(tmp_path)
    changes = compare(before, after)
    assert [c.name for c in changes] == ["c/z", "b/y", "a/x"]
    # a 10% slip is drift, a doubling is a regression
    assert [c.name for c in regressions(changes, tolerance=0.5)] == ["c/z"]
    assert [c.name for c in regressions(changes, tolerance=0.05)] == ["c/z", "b/y"]


def test_table_hides_metrics_that_did_not_move(tmp_path):
    write_run(tmp_path, "20260101T000000Z-aaaaaaa", {"moved/x": 1.0, "still/y": 5.0})
    write_run(tmp_path, "20260102T000000Z-bbbbbbb", {"moved/x": 2.0, "still/y": 5.0})
    runs = read_runs(tmp_path)

    quiet = table(runs)
    assert "moved/x" in quiet and "still/y" not in quiet
    assert "변화 없는 지표 1개" in quiet
    assert "still/y" in table(runs, changed_only=False)
    assert "moved/x" not in table(runs, pattern="still")


def test_check_exits_nonzero_only_on_a_real_regression(tmp_path, capsys):
    write_run(tmp_path, "20260101T000000Z-aaaaaaa", {"a/x": 1.0})
    write_run(tmp_path, "20260102T000000Z-bbbbbbb", {"a/x": 1.02})
    assert main(["--dir", str(tmp_path), "--check"]) == 0  # 2% is inside tolerance

    write_run(tmp_path, "20260103T000000Z-ccccccc", {"a/x": 3.0})
    assert main(["--dir", str(tmp_path), "--check"]) == 1
    assert "a/x" in capsys.readouterr().out


def test_scoreboard_without_any_runs_says_so(tmp_path, capsys):
    assert main(["--dir", str(tmp_path)]) == 0
    assert "기록된 런이 없다" in capsys.readouterr().out
