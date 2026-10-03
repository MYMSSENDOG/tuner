"""The recorder streams to disk: memory does not grow with the take.

The old recorder held every block in memory until stop — no ceiling, a full
encode on the GUI thread at stop, and a window where the last blocks could be
dropped. These pin the behaviour that removes all three: the take is on disk
and growing while it is played.
"""

import time

import numpy as np
import pytest
import soundfile as sf

from tuner.app import recorder

SR = 44100
BLOCK = 256


def feed(rec, seconds: float, value: float = 0.2) -> None:
    block = np.full(BLOCK, value, dtype=np.float32)
    for _ in range(int(seconds * SR / BLOCK)):
        rec.push_block(block, SR)


def part_files(directory):
    return list(directory.glob(f"*{recorder.PART_SUFFIX}"))


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_the_take_is_on_disk_while_it_is_being_played(tmp_path):
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    feed(rec, 3.0)
    # the writer opens the file on its own thread, on the first block it takes
    assert wait_for(lambda: len(part_files(tmp_path)) == 1), "nothing written while recording"
    part = part_files(tmp_path)[0]
    assert wait_for(lambda: part.stat().st_size > 1000), "the file is not growing"
    assert rec.running
    rec.discard()


def test_a_take_in_progress_is_never_listed(tmp_path):
    """Half a file is not a take."""
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    feed(rec, 1.0)
    assert recorder.list_takes(tmp_path) == []
    rec.discard()


def test_stop_writes_everything_that_was_handed_over(tmp_path):
    """Nothing between the last push and stop is lost — the old recorder could
    drop the blocks appended between joining the take and clearing it."""
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    feed(rec, 1.0)
    pushed = int(1.0 * SR / BLOCK) * BLOCK
    seconds = rec.stop()
    assert seconds == pytest.approx(pushed / SR)
    path = rec.finish("전부")
    assert sf.info(str(path)).frames == pushed


def test_saving_is_a_rename_not_an_encode(tmp_path):
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    feed(rec, 2.0)
    rec.stop()
    part = part_files(tmp_path)[0]
    size = part.stat().st_size
    path = rec.finish("이름")
    assert not part.exists()
    assert path.stat().st_size == size
    assert recorder.is_ours(path)


def test_discard_leaves_nothing_behind(tmp_path):
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    feed(rec, 0.5)
    rec.stop()
    rec.discard()
    assert list(tmp_path.iterdir()) == []
    assert not rec.has_take


def test_discard_while_running_leaves_nothing_behind(tmp_path):
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    feed(rec, 0.5)
    rec.discard()
    assert not rec.running
    assert list(tmp_path.iterdir()) == []


def test_a_block_after_stop_never_reaches_the_next_take(tmp_path):
    """The audio thread can hand over one more block in the instant after
    stop. It must not become the first block of the take that follows."""
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    feed(rec, 0.2, value=0.9)
    old_queue = rec._queue
    rec.stop()
    rec.discard()
    old_queue.put((np.full(BLOCK, 0.9, dtype=np.float32), SR))  # the straggler

    rec.start()
    feed(rec, 0.2, value=0.1)
    rec.stop()
    path = rec.finish("다음")
    signal, _ = sf.read(str(path))
    assert np.max(np.abs(signal)) < 0.5, "the previous take leaked into this one"


def test_a_failed_write_is_reported_not_swallowed(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(recorder, "open_signed", broken)
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    feed(rec, 0.2)
    with pytest.raises(RuntimeError, match="disk full"):
        rec.stop()
    assert not rec.has_take
    assert part_files(tmp_path) == []


def test_finishing_an_empty_take_is_refused(tmp_path):
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    rec.stop()
    with pytest.raises(RuntimeError):
        rec.finish("빈 것")
    assert list(tmp_path.iterdir()) == []
