"""Display trace: the record of what the meter showed, and its diff.

The trace is only worth anything if it is the app's own output rather than a
reimplementation of it, so the first test pins exactly that.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import soundfile as sf

from tests.fakes import FakeAudioInput
from tests.synth import SR, sequence, tone
from tuner.analysis.trace import Trace, TraceFrame
from tuner.analysis.trace import write_jsonl as write_trace
from tuner.app.engine import TunerEngine, TunerReading
from tuner.core.detector import YinDetector
from tuner.core.notes import note_to_freq
from tuner.tools.trace import (
    BRIEF_FRAMES,
    brief_flashes,
    diff,
    label_segments,
    main,
    read_jsonl,
    trace_signal,
    write_jsonl,
)


def two_notes() -> np.ndarray:
    return sequence([note_to_freq("A", 4), note_to_freq("C", 5)], 0.4, instrument="violin")


def test_trace_is_the_app_pipeline_not_a_copy():
    """Same signal through the engine directly and through the tracer must
    produce the same displayed notes, frame for frame."""
    signal = two_notes()
    readings: list[TunerReading] = []
    fake = FakeAudioInput(signal)
    engine = TunerEngine(fake, readings.append)
    engine.start()
    fake.pump()
    engine.stop()

    traced = trace_signal(signal, SR)
    assert len(traced.frames) == len(readings) > 0
    for frame, reading in zip(traced.frames, readings, strict=True):
        note = reading.note
        assert frame.label == (note.label if note else None)
        assert frame.state == reading.state.value
        if note is not None:
            assert frame.cents == round(note.cents, 3)


def test_trace_keeps_raw_detection_beside_the_displayed_value():
    """Both sides of the display policy are recorded — without the raw column
    a trace cannot tell 'the detector glitched' from 'the latch let it out'.
    """
    traced = trace_signal(tone(note_to_freq("A", 4), 0.5, instrument="violin"), SR)
    voiced = [f for f in traced.frames if f.hz is not None]
    assert len(voiced) > 10
    assert all(f.raw_hz is not None and f.confidence > 0.0 for f in voiced)
    # smoothing means the two columns are related but not identical
    assert any(f.raw_hz != f.hz for f in voiced)
    assert max(abs(1200 * np.log2(f.hz / 440.0)) for f in voiced) < 20.0


def test_trace_times_are_frame_end_times():
    signal = tone(note_to_freq("A", 4), 0.5)
    traced = trace_signal(signal, SR)
    times = [f.t_s for f in traced.frames]
    assert times == sorted(times)
    assert times[-1] <= len(signal) / SR
    # readings arrive exactly one detector hop apart once the pipeline is primed
    hop_s = YinDetector.hop_size / SR
    assert np.allclose(np.diff(times[1:]), hop_s, atol=2e-6)


def test_jsonl_roundtrip(tmp_path):
    traced = trace_signal(two_notes(), SR, audio="two_notes")
    path = tmp_path / "t.jsonl"
    write_jsonl(traced, path)
    back = read_jsonl(path)
    assert back == traced


def test_diff_locates_the_changed_stretch():
    traced = trace_signal(two_notes(), SR)
    frames = list(traced.frames)
    voiced = [i for i, f in enumerate(frames) if f.label is not None]
    changed = voiced[5:15]
    for i in changed:
        frames[i] = replace(frames[i], label="Z9")
    other = replace(traced, frames=frames)

    assert diff(traced, traced) == []
    spans = diff(traced, other)
    assert len(spans) == 1
    assert (spans[0].start, spans[0].end) == (changed[0], changed[-1])
    assert spans[0].frames == len(changed)
    assert spans[0].b == [("Z9", len(changed))]


def test_diff_ignores_differences_too_small_to_see():
    traced = trace_signal(two_notes(), SR)

    def shifted(by: float):
        return replace(
            traced,
            frames=[
                f if f.cents is None else replace(f, cents=f.cents + by)
                for f in traced.frames
            ],
        )

    assert diff(traced, shifted(0.2)) == []  # under CENTS_TOL: same number on screen
    assert diff(traced, shifted(2.0)) != []


def test_display_metrics_count_runs_not_frames():
    labels = [None, "A4", "A4", "A4", "C5", None, None, "A4", "A4"]
    assert label_segments(labels) == ["A4", "C5", "A4"]
    assert brief_flashes(labels, limit=2) == 1  # only the single-frame C5
    assert brief_flashes(labels, limit=BRIEF_FRAMES) == 3


def pinned_trace(raw_offset_cents: float, frames: int = 20) -> Trace:
    """A stretch showing D3 at the bottom of the meter, with the raw pitch
    sitting `raw_offset_cents` away from that name."""
    d3 = note_to_freq("D", 3)
    raw = d3 * 2 ** (raw_offset_cents / 1200.0)
    return Trace(
        audio="synthetic",
        sr=SR,
        detector="YIN (fast)",
        a4_hz=440.0,
        rev="test",
        frames=[
            TraceFrame(
                i=i,
                t_s=round(i * 256 / SR, 6),
                raw_hz=round(raw, 4),
                confidence=0.9,
                hz=round(d3, 4),
                label="D3",
                cents=-50.0,
                state="ok",
            )
            for i in range(frames)
        ],
    )


def test_explain_separates_an_honest_edge_from_a_clamped_hold():
    """The whole point of keeping all three numbers: -50 on screen means two
    completely different things depending on where the detector was."""
    from tuner.tools.trace import moments

    honest = moments(pinned_trace(-49.0))
    assert [m.kind for m in honest] == ["pinned"]
    assert "정직" in honest[0].verdict

    clamped = moments(pinned_trace(-1150.0))  # an octave-scale detection jump
    kinds = {m.kind for m in clamped}
    assert kinds == {"pinned", "held"}
    pinned = next(m for m in clamped if m.kind == "pinned")
    assert "래치" in pinned.verdict and "-1150" in pinned.raw


def test_explain_counts_where_the_policy_intervened():
    from tuner.tools.trace import explain

    text = explain(pinned_trace(-1150.0))
    assert "화면 이름이 검출과 다른 프레임 20" in text
    assert "주목할 순간" in text


def test_explain_stays_quiet_when_nothing_happened():
    from tuner.tools.trace import explain

    text = explain(trace_signal(tone(note_to_freq("A", 4), 0.6, instrument="violin"), SR))
    assert "주목할 순간 없음" in text


def test_cli_explains_a_saved_trace(tmp_path, capsys):
    path = tmp_path / "held.jsonl"
    write_trace(pinned_trace(-1150.0), path)
    assert main([str(path), "--explain"]) == 0
    assert "래치" in capsys.readouterr().out


def test_cli_writes_and_diffs(tmp_path, capsys):
    audio = tmp_path / "clip.wav"
    sf.write(audio, two_notes(), SR)
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"

    assert main([str(audio), "-o", str(first), "--quiet"]) == 0
    assert main([str(audio), "-o", str(second)]) == 0
    summary = capsys.readouterr().out
    assert "clip.wav" in summary and "세그먼트" in summary

    assert main(["--diff", str(first), str(second)]) == 0
    report = capsys.readouterr().out
    assert "차이 없음" in report  # same code, same audio: bit-identical display
