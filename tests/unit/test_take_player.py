"""The take player, against a fake output device.

What matters is the device's lifecycle: opened once when a take starts
playing, held open through pauses and the end of the take, closed when the
take is put away. Opening a stream is slow and, done from the wrong place,
deadlocks PortAudio; reopening it on every pause is an audible gap.
"""

import numpy as np
import pytest

from tests.fakes import FakeAudioOutput
from tuner.app.take_player import TakePlayer, resampled

SR = 44100


@pytest.fixture
def player(tmp_path):
    import soundfile as sf

    path = tmp_path / "take.wav"
    sf.write(str(path), np.full(SR, 0.25, dtype=np.float32), SR)
    output = FakeAudioOutput(sr=SR)
    p = TakePlayer(output)
    p.load_file(path)
    return p, output


def test_loading_does_not_open_the_device(player):
    p, output = player
    assert p.loaded and not p.playing
    assert output.start_count == 0


def test_pausing_and_resuming_keeps_one_stream(player):
    """It reopened the device on every resume: three presses, three streams."""
    p, output = player
    p.play()
    for _ in range(3):
        p.pause()
        p.play()
    assert output.start_count == 1
    assert output.stop_count == 0


def test_playing_again_after_the_end_keeps_the_stream(player):
    p, output = player
    p.play()
    output.pull(SR + 1000)  # past the end
    assert not p.playing and p.finished
    p.play()
    assert p.playing
    assert p.position == 0, "play at the end starts the take over"
    assert output.start_count == 1


def test_stop_closes_the_device_once(player):
    p, output = player
    p.play()
    p.pause()
    p.stop()
    p.stop()
    assert output.stop_count == 1
    assert not p.loaded


def test_stopping_a_take_that_never_played_opens_nothing(player):
    p, output = player
    p.stop()
    assert output.start_count == 0
    assert output.stop_count == 0


def test_paused_output_is_silence_and_does_not_advance(player):
    p, output = player
    p.play()
    output.pull(1000)
    p.pause()
    at = p.position
    assert not np.any(output.pull(512))
    assert p.position == at


def test_seeking_lands_where_asked(player):
    p, _ = player
    p.seek_seconds(0.5)
    assert p.position_seconds == pytest.approx(0.5, abs=1 / SR)


def test_resampling_keeps_the_duration():
    signal = np.sin(np.linspace(0, 100, 48000))
    out = resampled(signal, 48000, 44100)
    assert len(out) == 44100
