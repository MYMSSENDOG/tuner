"""Wall 6 — a recording is the sound that was played, and nothing else.

Four propositions, none of which depends on a threshold, a fixture or a tuning
value. They are what "the recording works" means, so they are the first thing
to write and the last thing to relax (docs/process/ground-truth.md: this area's
answer is arithmetic — the signal we synthesised is the signal that must come
back, and no annotator is needed to say so).

The one number here that looks like a gate is the +-2 cents at the bottom, and
it is this project's stated accuracy criterion, not a knob. If lossy encoding
ever costs more than that, the format is wrong, not the number.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import soundfile as sf

from tests.synth import SR, sequence, tone
from tuner.app import recorder
from tuner.core.detector import YinDetector
from tuner.core.notes import note_to_freq

# Half a frame of slack at both ends of the encode/decode round trip. Vorbis
# is block-based, so the decoded length lands within a block of the input.
LENGTH_SLACK_S = 0.03

# The project's accuracy criterion (tests/dsp/test_pitch_accuracy.py). Measured
# through the Vorbis round trip, the worst case is far under it.
MAX_ERROR_CENTS = 2.0


def detected_hz(signal: np.ndarray, sr: int) -> float | None:
    detector = YinDetector()
    confident = []
    for start in range(0, len(signal) - detector.frame_size + 1, detector.hop_size):
        result = detector.detect(signal[start : start + detector.frame_size], sr)
        if result.freq_hz is not None and result.confidence >= 0.5:
            confident.append(result.freq_hz)
    return float(np.median(confident)) if confident else None


def read_back(path) -> tuple[np.ndarray, int]:
    signal, sr = sf.read(str(path), always_2d=True)
    return signal.mean(axis=1), int(sr)


# --- 1. the file is as long as the take ---------------------------------------


@pytest.mark.parametrize("seconds", [0.4, 1.0, 2.5])
def test_the_file_lasts_as_long_as_what_was_recorded(tmp_path, seconds):
    rec = recorder.Recorder()
    rec.start()
    signal = tone(note_to_freq("A", 4), seconds, instrument="violin")
    for start in range(0, len(signal), 256):
        rec.push_block(signal[start : start + 256], SR)
    collected = rec.stop()

    path = recorder.save_take(collected, SR, f"len {seconds}", directory=tmp_path)
    assert sf.info(str(path)).duration == pytest.approx(seconds, abs=LENGTH_SLACK_S)


def test_pausing_shortens_the_file_by_exactly_what_was_skipped(tmp_path):
    """A pause is an absence, not silence. Two seconds of playing with a
    five-second pause in the middle is a two-second file — anything else would
    put a hole in the take and lie about how long the performance was."""
    played = tone(note_to_freq("A", 4), 1.0, instrument="violin")
    skipped = tone(note_to_freq("C", 5), 5.0, instrument="violin")

    rec = recorder.Recorder()
    rec.start()
    for start in range(0, len(played), 256):
        rec.push_block(played[start : start + 256], SR)
    rec.pause()
    for start in range(0, len(skipped), 256):
        rec.push_block(skipped[start : start + 256], SR)
    rec.resume()
    for start in range(0, len(played), 256):
        rec.push_block(played[start : start + 256], SR)

    path = recorder.save_take(rec.stop(), SR, "paused", directory=tmp_path)
    assert sf.info(str(path)).duration == pytest.approx(2.0, abs=LENGTH_SLACK_S)


@pytest.mark.parametrize("pauses", [1, 4, 9])
def test_any_number_of_pauses_still_adds_up(tmp_path, pauses):
    """Whatever the player does with the pause key, the file is the sum of the
    stretches that were recorded."""
    piece = tone(note_to_freq("G", 3), 0.2, instrument="violin")
    rec = recorder.Recorder()
    rec.start()
    for _ in range(pauses + 1):
        for start in range(0, len(piece), 256):
            rec.push_block(piece[start : start + 256], SR)
        rec.pause()
        rec.push_block(np.zeros(SR, dtype=np.float32), SR)  # ignored
        rec.resume()

    expected = 0.2 * (pauses + 1)
    assert rec.seconds == pytest.approx(expected, abs=0.01)
    path = recorder.save_take(rec.stop(), SR, f"p{pauses}", directory=tmp_path)
    assert sf.info(str(path)).duration == pytest.approx(expected, abs=LENGTH_SLACK_S)


# --- 2. the pitch survives the encoding ----------------------------------------


@pytest.mark.parametrize("name,octave", [("E", 2), ("G", 3), ("A", 4), ("E", 6)])
def test_lossy_encoding_does_not_move_the_pitch(tmp_path, name, octave):
    """Vorbis throws away what the ear does not need, and a tuner is allowed to
    care only if it moves the fundamental. It must not: a take the player
    re-analyses later has to read the same as the moment it was played.
    """
    truth = note_to_freq(name, octave)
    original = tone(truth, 1.0, instrument="violin")
    path = recorder.save_take(original, SR, f"{name}{octave}", directory=tmp_path)

    decoded, sr = read_back(path)
    detected = detected_hz(decoded, sr)
    assert detected is not None, "nothing detectable survived the encode"
    error = 1200.0 * math.log2(detected / truth)
    assert abs(error) <= MAX_ERROR_CENTS, f"{name}{octave}: {error:+.2f} cents after Vorbis"


def test_a_phrase_survives_the_encoding(tmp_path):
    """Not just a steady tone: note changes are where an encoder's transient
    handling would show up if it were going to."""
    freqs = [note_to_freq(n, o) for n, o in (("G", 3), ("B", 3), ("D", 4), ("G", 4))]
    original = sequence(freqs, note_duration=0.4, instrument="violin")
    path = recorder.save_take(original, SR, "phrase", directory=tmp_path)
    decoded, sr = read_back(path)

    assert len(decoded) / sr == pytest.approx(len(original) / SR, abs=LENGTH_SLACK_S)
    for i, truth in enumerate(freqs):
        start = int((i * 0.42 + 0.1) * sr)
        window = decoded[start : start + int(0.2 * sr)]
        detected = detected_hz(window, sr)
        assert detected is not None
        assert abs(1200.0 * math.log2(detected / truth)) <= MAX_ERROR_CENTS


# --- 3. only our files are ours -------------------------------------------------


def test_an_unsigned_file_in_the_folder_is_invisible(tmp_path):
    """The takes live in the player's music folder, where other audio legitimately
    turns up. Membership of the folder therefore cannot be the test."""
    sf.write(
        str(tmp_path / "album track.ogg"),
        tone(note_to_freq("A", 4), 0.5),
        SR,
        format=recorder.FORMAT,
        subtype=recorder.SUBTYPE,
    )
    ours = recorder.save_take(tone(note_to_freq("A", 4), 0.5), SR, "ours", directory=tmp_path)

    listed = recorder.list_takes(tmp_path)
    assert [t.path for t in listed] == [ours]


def test_the_signature_survives_being_moved(tmp_path):
    """It is in the file, not in an index — so a take stays ours wherever it
    goes, and there is no index to fall out of step with the folder."""
    elsewhere = tmp_path / "moved"
    elsewhere.mkdir()
    path = recorder.save_take(tone(note_to_freq("A", 4), 0.4), SR, "moved", directory=tmp_path)
    moved = path.rename(elsewhere / path.name)
    assert recorder.is_ours(moved)
    assert [t.title for t in recorder.list_takes(elsewhere)] == ["moved"]


def test_the_signature_check_would_reject_something(tmp_path):
    """Power check: a test that calls everything ours would pass the file above
    just as happily."""
    plain = tmp_path / "plain.ogg"
    sf.write(str(plain), tone(440.0, 0.3), SR, format=recorder.FORMAT, subtype=recorder.SUBTYPE)
    assert not recorder.is_ours(plain)
    signed = recorder.save_take(tone(440.0, 0.3), SR, "signed", directory=tmp_path)
    assert recorder.is_ours(signed)


# --- 4. nothing is ever overwritten ----------------------------------------------


def test_saving_never_destroys_an_earlier_take(tmp_path):
    """The default title has one-minute resolution, so two takes wanting the
    same name is ordinary. Losing a performance to it would not be."""
    paths = [recorder.save_take(tone(440.0, 0.3), SR, "같은 이름", directory=tmp_path) for _ in range(5)]
    assert len(set(paths)) == 5
    assert all(path.exists() for path in paths)
    assert len(recorder.list_takes(tmp_path)) == 5
