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


# --- listening back while paused ---------------------------------------------


def tone_blocks(rec, seconds: float, freq: float = 440.0):
    t = np.arange(int(seconds * SR)) / SR
    signal = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    usable = len(signal) // BLOCK * BLOCK
    for start in range(0, usable, BLOCK):
        rec.push_block(signal[start : start + BLOCK], SR)
    return signal[:usable]


def test_a_paused_take_can_be_heard_to_its_last_sample(tmp_path):
    """The still-open file is missing the encoder's look-ahead (~0.2s); the
    raw tail must fill exactly that, so the last note is not cut off."""
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    played = tone_blocks(rec, 3.0)
    rec.pause()
    heard = rec.so_far()
    assert len(heard) == len(played)
    # the end is the raw audio, sample for sample
    assert np.allclose(heard[-SR // 10 :], played[-SR // 10 :])
    rec.discard()


def test_listening_back_does_not_disturb_the_take(tmp_path):
    rec = recorder.Recorder(directory=tmp_path)
    rec.start()
    first = tone_blocks(rec, 1.0)
    rec.pause()
    rec.so_far()
    rec.resume()
    second = tone_blocks(rec, 1.0, freq=660.0)
    rec.stop()
    path = rec.finish("이어서")
    assert sf.info(str(path)).frames == len(first) + len(second)


def test_listening_back_is_only_for_a_paused_take(tmp_path):
    rec = recorder.Recorder(directory=tmp_path)
    with pytest.raises(RuntimeError):
        rec.so_far()  # nothing running
    rec.start()
    tone_blocks(rec, 0.5)
    with pytest.raises(RuntimeError):
        rec.so_far()  # running, not paused: stale before it returned
    rec.discard()
