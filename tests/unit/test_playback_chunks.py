"""Chunk-serving state machine of file playback (no audio device needed).

The demo and compare tools feed the tuner exactly what the speakers play;
gapless looping and end-of-file padding are what make that trustworthy.
"""

import numpy as np
import pytest
import soundfile as sf

from tuner.tools.playback import SharedPlayback


@pytest.fixture
def short_file(tmp_path):
    path = tmp_path / "ramp.wav"
    sf.write(path, np.arange(1000, dtype=np.float32) / 1000.0, 44100)
    return str(path)


def test_chunks_reproduce_the_file_exactly(short_file):
    playback = SharedPlayback(short_file)
    out = np.concatenate([playback.next_chunk(256) for _ in range(4)])[:1000]
    expected = np.arange(1000, dtype=np.float32) / 1000.0
    assert np.allclose(out, expected, atol=1 / 32768)  # PCM16 round-trip


def test_end_of_file_pads_with_silence_forever(short_file):
    playback = SharedPlayback(short_file, loop=False)
    playback.next_chunk(1000)
    for _ in range(3):
        assert not playback.next_chunk(256).any()


def test_loop_wraps_gaplessly(short_file):
    playback = SharedPlayback(short_file, loop=True)
    chunk = np.concatenate([playback.next_chunk(300) for _ in range(4)])  # 1200 > 1000
    # sample 1000 must be the file's first sample again, with no zero gap
    expected_wrap = np.arange(200, dtype=np.float32) / 1000.0
    assert np.allclose(chunk[1000:], expected_wrap, atol=1 / 32768)


def test_chunks_always_have_requested_length(short_file):
    playback = SharedPlayback(short_file, loop=True)
    assert all(len(playback.next_chunk(n)) == n for n in (1, 255, 256, 257, 999, 1001))
