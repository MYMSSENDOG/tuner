"""The recorder and its storage, without a window.

Everything here is arithmetic or a file on disk, which is the whole reason
`app/recorder.py` is Qt-free: the part that decides what gets written must be
testable without a display.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tuner.app import recorder

SR = 44100


def block(seconds: float, value: float = 0.1) -> np.ndarray:
    return np.full(int(SR * seconds), value, dtype=np.float32)


def tone(seconds: float, freq: float = 440.0, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# --- collecting --------------------------------------------------------------


def test_nothing_is_collected_before_start():
    rec = recorder.Recorder()
    rec.push_block(block(0.1), SR)
    assert not rec.running
    assert len(rec.take()) == 0


def test_a_take_is_what_was_pushed():
    rec = recorder.Recorder()
    rec.start()
    rec.push_block(block(0.1), SR)
    rec.push_block(block(0.2), SR)
    assert rec.seconds == pytest.approx(0.3, abs=1e-6)
    assert len(rec.stop()) == int(SR * 0.3)
    assert not rec.running


def test_a_paused_stretch_is_absent_rather_than_silent():
    """Pausing is not appending, so the gap does not exist in the result.

    The alternative — recording silence through the pause — would make the
    file longer than the performance and put a hole in the middle of it.
    """
    rec = recorder.Recorder()
    rec.start()
    rec.push_block(block(0.1, 0.5), SR)
    rec.pause()
    rec.push_block(block(5.0, 0.5), SR)  # a long absence
    rec.resume()
    rec.push_block(block(0.1, 0.5), SR)
    signal = rec.stop()
    assert len(signal) == int(SR * 0.2)
    assert np.all(signal != 0.0), "no silence was recorded"


def test_pause_toggles():
    rec = recorder.Recorder()
    rec.start()
    assert rec.toggle_pause() is True
    assert rec.paused
    assert rec.toggle_pause() is False
    assert not rec.paused


def test_starting_again_abandons_the_previous_take():
    rec = recorder.Recorder()
    rec.start()
    rec.push_block(block(0.5), SR)
    rec.start()
    assert rec.seconds == 0.0


def test_discard_keeps_nothing():
    rec = recorder.Recorder()
    rec.start()
    rec.push_block(block(0.3), SR)
    rec.discard()
    assert not rec.running
    assert len(rec.take()) == 0


def test_blocks_are_copied_not_referenced():
    """The audio layer reuses its buffers; keeping the reference would mean
    recording whatever happened to be in them later."""
    rec = recorder.Recorder()
    rec.start()
    buffer = block(0.05, 0.4)
    rec.push_block(buffer, SR)
    buffer[:] = 0.0
    assert np.all(rec.take() == pytest.approx(0.4))


# --- titles and paths ---------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("바흐 프렐류드", "바흐 프렐류드"),
        ("A/B", "A B"),  # becomes two words, not "AB"
        ("  공백   많은   제목 ", "공백 많은 제목"),
        ('bad:"name"?', "bad name"),
        ("trailing dots...", "trailing dots"),
    ],
)
def test_titles_survive_becoming_filenames(given, expected):
    assert recorder.safe_title(given) == expected


def test_an_empty_title_falls_back_to_the_default():
    assert recorder.safe_title("   ") == recorder.default_title()
    assert recorder.safe_title("///") == recorder.default_title()


def test_a_long_title_is_cut_not_rejected():
    assert len(recorder.safe_title("가" * 500)) == recorder.MAX_TITLE_CHARS


def test_the_default_title_is_a_date_and_time():
    # local time on purpose: the name is for a person reading their own folder
    stamp = datetime(2026, 10, 3, 21, 47, tzinfo=UTC).astimezone()
    assert recorder.default_title(stamp) == stamp.strftime("%Y-%m-%d %H-%M")


def test_a_second_take_with_the_same_name_does_not_overwrite(tmp_path):
    first = recorder.save_take(tone(0.2), SR, "같은 이름", directory=tmp_path)
    second = recorder.save_take(tone(0.2), SR, "같은 이름", directory=tmp_path)
    assert first != second
    assert first.exists() and second.exists()
    assert second.stem == "같은 이름 (2)"


# --- writing and reading back --------------------------------------------------


def test_a_saved_take_is_ours_and_lists(tmp_path):
    path = recorder.save_take(tone(0.5), SR, "연습", directory=tmp_path, a4_hz=442.0)
    assert path.suffix == recorder.SUFFIX
    assert recorder.is_ours(path)
    takes = recorder.list_takes(tmp_path)
    assert [t.title for t in takes] == ["연습"]
    assert takes[0].seconds == pytest.approx(0.5, abs=0.02)


def test_the_file_is_as_long_as_what_was_recorded(tmp_path):
    """The one promise a recorder cannot break."""
    signal = tone(1.25)
    path = recorder.save_take(signal, SR, "길이", directory=tmp_path)
    assert sf.info(str(path)).duration == pytest.approx(len(signal) / SR, abs=0.02)


def test_the_signature_carries_the_reference_pitch(tmp_path):
    """So a take can be re-analysed later against the A4 it was played at."""
    path = recorder.save_take(tone(0.3), SR, "기준", directory=tmp_path, a4_hz=442.0)
    with sf.SoundFile(str(path)) as handle:
        assert handle.software.startswith(recorder.SIGNATURE)
        meta = json.loads(handle.comment)
    assert meta["app"] == recorder.SIGNATURE
    assert meta["a4_hz"] == pytest.approx(442.0)
    assert meta["at"]


def test_a_file_we_did_not_write_is_not_listed(tmp_path):
    """The folder is somewhere a person browses (it is their music folder), so
    membership cannot be the test — only the signature can."""
    sf.write(
        str(tmp_path / "someone-elses.ogg"),
        tone(0.3),
        SR,
        format=recorder.FORMAT,
        subtype=recorder.SUBTYPE,
    )
    ours = recorder.save_take(tone(0.3), SR, "우리 것", directory=tmp_path)

    assert not recorder.is_ours(tmp_path / "someone-elses.ogg")
    assert [t.title for t in recorder.list_takes(tmp_path)] == [ours.stem]


def test_a_file_that_is_not_audio_is_ignored(tmp_path):
    (tmp_path / "notes.ogg").write_text("this is not audio", encoding="utf-8")
    assert not recorder.is_ours(tmp_path / "notes.ogg")
    assert recorder.list_takes(tmp_path) == []


def test_an_empty_take_is_refused(tmp_path):
    with pytest.raises(RuntimeError):
        recorder.save_take(np.zeros(0, dtype=np.float32), SR, "빈 것", directory=tmp_path)


def test_listing_a_missing_folder_is_empty_not_an_error(tmp_path):
    assert recorder.list_takes(tmp_path / "nothing here") == []


def test_takes_are_listed_newest_first(tmp_path):
    import os
    import time

    first = recorder.save_take(tone(0.2), SR, "먼저", directory=tmp_path)
    recorder.save_take(tone(0.2), SR, "나중", directory=tmp_path)
    # mtimes can land in the same tick; make the order unambiguous
    os.utime(first, (time.time() - 100, time.time() - 100))
    assert [t.title for t in recorder.list_takes(tmp_path)] == ["나중", "먼저"]


def test_renaming_moves_the_file_because_the_title_is_the_filename(tmp_path):
    path = recorder.save_take(tone(0.2), SR, "원래 이름", directory=tmp_path)
    renamed = recorder.rename_take(path, "새 이름")
    assert not path.exists()
    assert renamed.stem == "새 이름"
    assert [t.title for t in recorder.list_takes(tmp_path)] == ["새 이름"]


def test_renaming_onto_an_existing_name_does_not_clobber_it(tmp_path):
    keep = recorder.save_take(tone(0.2), SR, "지켜질 것", directory=tmp_path)
    other = recorder.save_take(tone(0.2), SR, "바뀔 것", directory=tmp_path)
    renamed = recorder.rename_take(other, "지켜질 것")
    assert keep.exists()
    assert renamed != keep
    assert len(recorder.list_takes(tmp_path)) == 2


# --- the folder ----------------------------------------------------------------


def test_the_folder_can_be_pointed_elsewhere_for_tests(tmp_path, monkeypatch):
    """Without this the suite would write into the real music folder."""
    monkeypatch.setenv(recorder.ENV_DIR, str(tmp_path / "takes"))
    assert recorder.recordings_dir() == tmp_path / "takes"


def test_the_folder_is_under_music_and_not_hidden(monkeypatch):
    """A dotfolder is right for developer reports and wrong for a person's own
    performances — they are meant to be found, mailed and backed up."""
    monkeypatch.delenv(recorder.ENV_DIR, raising=False)
    directory = recorder.recordings_dir()
    assert directory.name == "Tuner"
    assert not any(part.startswith(".") for part in directory.parts)
    assert "Music" in directory.parts or "music" in {p.lower() for p in directory.parts}


# --- small readouts -------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,shown", [(0, "0:00"), (9.9, "0:09"), (84, "1:24"), (3725, "62:05")]
)
def test_duration_reads_as_minutes_and_seconds(seconds, shown):
    assert recorder.Take(Path("x"), "x", seconds).duration_text == shown


@pytest.mark.parametrize(
    "size,shown", [(512, "512 B"), (2048, "2 KB"), (5 * 1024 * 1024, "5.0 MB")]
)
def test_sizes_read_as_people_write_them(size, shown):
    assert recorder.human_size(size) == shown
