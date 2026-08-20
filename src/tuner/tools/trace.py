"""What the meter displayed, frame by frame — and the diff between two runs.

    python -m tuner.tools.trace tests/fixtures/audio/oboe_scale_C4B4.aiff
    python -m tuner.tools.trace some.wav --vs HEAD~1      # 전후 비교
    python -m tuner.tools.trace --diff before.jsonl after.jsonl

The tuner's output is not a number, it is a *sequence*: which name stood on
screen, for how long, with what deviation. Scalar test metrics cannot show
where two versions started disagreeing; this can.

The recording is fed through the real TunerEngine offline — no Qt, no audio
device, no real-time wait — so a whole fixture traces in a fraction of its
duration and the result is deterministic enough to diff.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tuner.analysis.trace import (
    BRIEF_FRAMES,
    Trace,
    TraceFrame,
    brief_flashes,
    code_revision,
    label_runs,
    label_segments,
    read_jsonl,
    write_jsonl,
)
from tuner.app.engine import TunerEngine, TunerReading
from tuner.audio.input import BlockCallback, InputDevice
from tuner.core.detector import PitchDetector, SpectralDetector, YinDetector
from tuner.core.pitch import PitchResult

BLOCK_SIZE = 256  # what the real device delivers; keeps engine timing identical

DETECTOR_CHOICES: dict[str, type] = {"yin": YinDetector, "spectral": SpectralDetector}


# ---------------------------------------------------------------- production


class _OfflineInput:
    """AudioInput that hands the engine a signal as fast as it can take it."""

    def __init__(self, sr: int):
        self._sr = sr
        self._callback: BlockCallback | None = None

    def list_devices(self) -> list[InputDevice]:
        return []

    def refresh_devices(self) -> None:
        pass  # the source cannot gain devices

    def start(self, device_id: int | None, callback: BlockCallback) -> int:
        self._callback = callback
        return self._sr

    def stop(self) -> None:
        self._callback = None

    def push(self, block: np.ndarray) -> None:
        if self._callback is None:
            raise RuntimeError("push before start()")
        self._callback(block)


class _Recording:
    """PitchDetector decorator that keeps every raw result.

    The trace has to show what the display policy was *given*, not only what
    it produced — that is the difference between "the detector glitched" and
    "the latch let a glitch through". The engine already accepts a detector,
    so this needs no engine change.
    """

    def __init__(self, inner: PitchDetector):
        self._inner = inner
        self.name: str = inner.name
        self.frame_size: int = inner.frame_size
        self.hop_size: int = inner.hop_size
        self.center_offset: int = inner.center_offset
        self.results: list[PitchResult] = []

    def detect(self, frame: np.ndarray, sr: int) -> PitchResult:
        result = self._inner.detect(frame, sr)
        self.results.append(result)
        return result


def trace_signal(
    signal: np.ndarray,
    sr: int,
    *,
    audio: str = "<signal>",
    detector: PitchDetector | None = None,
    a4_hz: float = 440.0,
) -> Trace:
    """Run the real pipeline over a signal, recording every displayed frame."""
    recorder = _Recording(detector or YinDetector())
    source = _OfflineInput(sr)
    frames: list[TraceFrame] = []
    fed = 0

    def on_reading(reading: TunerReading) -> None:
        raw = recorder.results[-1]
        note = reading.note
        frames.append(
            TraceFrame(
                i=len(frames),
                t_s=round(fed / sr, 6),  # microseconds: a hop is 5.8ms, so 1e-5 would blur it
                raw_hz=None if raw.freq_hz is None else round(raw.freq_hz, 4),
                confidence=round(raw.confidence, 4),
                hz=None if note is None else round(note.freq_hz, 4),
                label=None if note is None else note.label,
                cents=None if note is None else round(note.cents, 3),
                state=reading.state.value,
            )
        )

    engine = TunerEngine(source, on_reading, detector=recorder)
    engine.set_a4(a4_hz)
    engine.start()
    for start in range(0, len(signal), BLOCK_SIZE):
        block = signal[start : start + BLOCK_SIZE]
        fed += len(block)  # the callback below runs on this block: frame end
        source.push(block)
    engine.stop()
    return Trace(
        audio=audio,
        sr=sr,
        detector=recorder.name,
        a4_hz=a4_hz,
        rev=code_revision(),
        frames=frames,
    )


def trace_file(
    path: str | Path,
    *,
    detector: PitchDetector | None = None,
    a4_hz: float = 440.0,
) -> Trace:
    import soundfile as sf

    signal, sr = sf.read(str(path), always_2d=True)
    return trace_signal(
        signal.mean(axis=1),
        int(sr),
        audio=Path(path).name,
        detector=detector,
        a4_hz=a4_hz,
    )


# --------------------------------------------------------------------- diff

# Below this the two runs are showing the same number: the meter's own scale
# is 100 cents wide across ~200 pixels, so half a cent cannot be seen. Above
# it, a difference is something the user could in principle notice.
CENTS_TOL = 0.5


@dataclass(frozen=True)
class Divergence:
    """A stretch of frames where the two traces disagree."""

    start: int
    end: int  # inclusive
    t_start: float
    t_end: float
    a: list[tuple[str, int]]  # what A showed, as (label, frames) runs
    b: list[tuple[str, int]]
    max_cents: float  # largest cent gap inside the stretch

    @property
    def frames(self) -> int:
        return self.end - self.start + 1


def frames_agree(a: TraceFrame, b: TraceFrame, cents_tol: float = CENTS_TOL) -> bool:
    if a.label != b.label or a.state != b.state:
        return False
    if (a.cents is None) != (b.cents is None):
        return False
    if a.cents is None or b.cents is None:
        return True
    return abs(a.cents - b.cents) <= cents_tol


def diff(a: Trace, b: Trace, cents_tol: float = CENTS_TOL) -> list[Divergence]:
    """Stretches where two traces of the same audio disagree.

    Frame-aligned: same audio and same hop means frame i of each describes the
    same instant. A shorter trace's tail counts as divergence (a changed hop
    size shifts everything, and the report should say so loudly).
    """
    spans: list[Divergence] = []
    n = max(len(a.frames), len(b.frames))
    i = 0
    while i < n:
        fa = a.frames[i] if i < len(a.frames) else None
        fb = b.frames[i] if i < len(b.frames) else None
        if fa is not None and fb is not None and frames_agree(fa, fb, cents_tol):
            i += 1
            continue
        start = i
        while i < n:
            fa = a.frames[i] if i < len(a.frames) else None
            fb = b.frames[i] if i < len(b.frames) else None
            if fa is not None and fb is not None and frames_agree(fa, fb, cents_tol):
                break
            i += 1
        spans.append(_divergence(a, b, start, i - 1))
    return spans


def _divergence(a: Trace, b: Trace, start: int, end: int) -> Divergence:
    slice_a = a.frames[start : end + 1]
    slice_b = b.frames[start : end + 1]
    times = [f.t_s for f in (slice_a or slice_b)]
    gaps = [
        abs(fa.cents - fb.cents)
        for fa, fb in zip(slice_a, slice_b, strict=False)
        if fa.cents is not None and fb.cents is not None
    ]
    return Divergence(
        start=start,
        end=end,
        t_start=times[0] if times else 0.0,
        t_end=times[-1] if times else 0.0,
        a=label_runs([f.label for f in slice_a]),
        b=label_runs([f.label for f in slice_b]),
        max_cents=max(gaps) if gaps else 0.0,
    )


def shifted(trace: Trace, frames: int) -> Trace:
    """The same trace with its first `frames` readings dropped."""
    return Trace(
        trace.audio, trace.sr, trace.detector, trace.a4_hz, trace.rev, trace.frames[frames:]
    )


# The engine primes its ring before it can detect anything: frame_size/hop
# readings' worth of audio (4096/256 = 15 by default). A live capture that
# began while the app was already running inherits a primed buffer, but
# replaying its audio from a file has to fill one first — so the replay is
# missing exactly those frames at the front, and a frame-aligned diff of the
# two is comparing different instants. Every session recording and every
# Ctrl+R ring capture starts mid-stream, so this is the normal case, not an
# exotic one: found on a real 36s session where 84.9% of frames "differed"
# while every display metric matched exactly.
MAX_ALIGN_FRAMES = 64


def best_shift(a: Trace, b: Trace, cents_tol: float = CENTS_TOL) -> int:
    """How far into `a` the first frame of `b` belongs."""
    best, best_score = 0, -1.0
    for shift in range(min(MAX_ALIGN_FRAMES, max(len(a.frames) - 10, 0)) + 1):
        pairs = list(zip(a.frames[shift:], b.frames, strict=False))
        if len(pairs) < 10:
            break
        score = sum(1 for fa, fb in pairs if frames_agree(fa, fb, cents_tol)) / len(pairs)
        if score > best_score:  # ties keep the smaller shift
            best, best_score = shift, score
    return best


def aligned_diff(a: Trace, b: Trace, cents_tol: float = CENTS_TOL) -> tuple[int, list[Divergence]]:
    """Diff two traces of the same audio, correcting for the priming gap."""
    shift = best_shift(a, b, cents_tol)
    return shift, diff(shifted(a, shift), b, cents_tol)


# ------------------------------------------------------------------ reports


def _runs_text(runs: list[tuple[str, int]], limit: int = 4) -> str:
    if not runs:
        return "-"
    shown = " ".join(f"{label}x{length}" for label, length in runs[:limit])
    return shown + (f" (+{len(runs) - limit})" if len(runs) > limit else "")


def summary(trace: Trace) -> str:
    labels = trace.labels
    segments = label_segments(labels)
    cents = [abs(f.cents) for f in trace.frames if f.cents is not None]
    silent = sum(1 for label in labels if label is None)
    return (
        f"{trace.audio} - {len(trace.frames)} frames, {trace.sr}Hz, "
        f"{trace.detector}, A4={trace.a4_hz:g}, rev {trace.rev}\n"
        f"  세그먼트 {len(segments)}, 짧은 표시(<{BRIEF_FRAMES}f) "
        f"{brief_flashes(labels)}, 무음 {silent}프레임, "
        f"|cent| 최대 {max(cents) if cents else 0.0:.1f}\n"
        f"  {' '.join(segments[:16])}{' ...' if len(segments) > 16 else ''}"
    )


def diff_report(a: Trace, b: Trace, spans: list[Divergence], top: int = 12) -> str:
    n = max(len(a.frames), len(b.frames))
    changed = sum(span.frames for span in spans)
    lines = [
        f"{a.audio}: A rev {a.rev} vs B rev {b.rev}",
        (
            f"  프레임 {len(a.frames)} / {len(b.frames)}, "
            f"다른 프레임 {changed}/{n} ({100.0 * changed / max(n, 1):.1f}%), "
            f"구간 {len(spans)}"
        ),
        (
            f"  세그먼트 {len(label_segments(a.labels))} -> {len(label_segments(b.labels))}, "
            f"짧은 표시 {brief_flashes(a.labels)} -> {brief_flashes(b.labels)}"
        ),
    ]
    if not spans:
        lines.append("  차이 없음: 표시가 프레임 단위로 동일하다.")
        return "\n".join(lines)
    for span in spans[:top]:
        lines.append(
            f"  [{span.start:5d}-{span.end:5d}] {span.t_start:6.2f}s..{span.t_end:6.2f}s "
            f"{span.frames:4d}f  A: {_runs_text(span.a)}  ->  B: {_runs_text(span.b)}"
            + (f"  cent차 {span.max_cents:.1f}" if span.max_cents else "")
        )
    if len(spans) > top:
        lines.append(f"  ... 그 외 {len(spans) - top}개 구간")
    return "\n".join(lines)


# ------------------------------------------------------------------ explain
# A reading carries three numbers: what the detector found, what the tracker
# decided to display, and what the latch named it. Most of the time they say
# the same thing. Where they disagree is exactly where the display policy is
# doing something — so naming those moments answers "why is it doing that".

EVENT_FRAMES = 4  # shorter than this is a blip, not an episode
HELD_CENTS = 60.0  # past this the raw pitch is not the name on screen at all


@dataclass(frozen=True)
class Moment:
    """A stretch where the display and the detection told different stories."""

    kind: str  # pinned | held | flash
    start: int
    end: int
    t_start: float
    shown: str
    raw: str
    verdict: str

    @property
    def frames(self) -> int:
        return self.end - self.start + 1


def _raw_offset_cents(frame: TraceFrame, a4_hz: float) -> float | None:
    """How far the raw detection sat from the name on screen."""
    from tuner.core.notes import NOTE_NAMES, note_to_freq

    if frame.raw_hz is None or frame.label is None:
        return None
    name = frame.label.rstrip("-0123456789")
    if name not in NOTE_NAMES:
        return None
    octave = int(frame.label[len(name) :])
    return 1200.0 * float(np.log2(frame.raw_hz / note_to_freq(name, octave, a4_hz)))


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    spans, start = [], None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            spans.append((start, i - 1))
            start = None
    if start is not None:
        spans.append((start, len(flags) - 1))
    return spans


def _moment(
    trace: Trace, offsets: list[float | None], span: tuple[int, int], kind: str, verdict: str
) -> Moment:
    start, end = span
    window = trace.frames[start : end + 1]
    cents = [f.cents for f in window if f.cents is not None]
    raws = [o for o in offsets[start : end + 1] if o is not None]
    names = "/".join(label_segments([f.label for f in window])[:3]) or "-"
    return Moment(
        kind=kind,
        start=start,
        end=end,
        t_start=window[0].t_s,
        shown=f"{names} {min(cents or [0.0]):+.0f}..{max(cents or [0.0]):+.0f}c",
        raw=f"{min(raws or [0.0]):+.0f}..{max(raws or [0.0]):+.0f}c",
        verdict=verdict,
    )


def moments(trace: Trace) -> list[Moment]:
    """Stretches where the display was not simply reporting the detection."""
    from tuner.core.notes import NOTE_HOLD_CENTS_CEILING

    frames = trace.frames
    offsets = [_raw_offset_cents(f, trace.a4_hz) for f in frames]
    found: list[Moment] = []

    # 1. the number parked at the edge of the meter
    pinned = [
        f.cents is not None and abs(f.cents) >= NOTE_HOLD_CENTS_CEILING - 0.5 for f in frames
    ]
    for span in _runs(pinned):
        if span[1] - span[0] + 1 < EVENT_FRAMES:
            continue
        window = [o for o in offsets[span[0] : span[1] + 1] if o is not None]
        far = bool(window) and max(abs(o) for o in window) > HELD_CENTS
        verdict = (
            "래치가 이름을 붙들고 숫자는 상한에 물림 (검출은 딴 데)"
            if far
            else "정직: 음정이 진짜 반음 경계에 있다"
        )
        found.append(_moment(trace, offsets, span, "pinned", verdict))

    # 2. the name on screen is not the note the detector was reporting
    for span in _runs([o is not None and abs(o) > HELD_CENTS for o in offsets]):
        if span[1] - span[0] + 1 >= EVENT_FRAMES:
            found.append(_moment(trace, offsets, span, "held", "래치가 검출 점프를 흡수 중"))

    # 3. names too short to read
    position = 0
    for label, length in label_runs(trace.labels):
        while position < len(frames) and frames[position].label is None:
            position += 1
        span = (position, position + length - 1)
        position += length
        if length < BRIEF_FRAMES:
            found.append(
                _moment(trace, offsets, span, "flash", f"{label} 이 {length}프레임만 떴다")
            )

    return sorted(found, key=lambda m: -m.frames)


def explain(trace: Trace, top: int = 12) -> str:
    """Why the meter showed what it showed, moment by moment."""
    frames = trace.frames
    offsets = [_raw_offset_cents(f, trace.a4_hz) for f in frames]
    voiced = [f for f in frames if f.label is not None]
    smoothed = sum(
        1
        for f in frames
        if f.hz is not None
        and f.raw_hz is not None
        and abs(1200.0 * float(np.log2(f.hz / f.raw_hz))) > 1.0
    )
    held = sum(1 for o in offsets if o is not None and abs(o) > HELD_CENTS)
    hop_ms = (frames[1].t_s - frames[0].t_s) * 1000 if len(frames) > 1 else 0.0
    total = max(len(frames), 1)

    lines = [
        summary(trace),
        "",
        (
            f"  프레임 {len(frames)} (표시 {len(voiced)}, "
            f"무음 {len(frames) - len(voiced)}), 홉 {hop_ms:.1f}ms"
        ),
        f"  스무딩이 1c 넘게 개입한 프레임 {smoothed} ({100.0 * smoothed / total:.1f}%)",
        f"  화면 이름이 검출과 다른 프레임 {held} ({100.0 * held / total:.1f}%)",
    ]
    found = moments(trace)
    if not found:
        lines.append("  주목할 순간 없음 - 표시가 검출을 그대로 따라갔다.")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"  주목할 순간 {len(found)}개 (긴 것부터):")
    for m in found[:top]:
        lines.append(
            f"  [{m.start:5d}-{m.end:5d}] {m.t_start:7.2f}s {m.frames:4d}f "
            f"{m.frames * hop_ms:6.0f}ms  {m.kind:<6} 화면 {m.shown:<20} "
            f"raw {m.raw:<16} {m.verdict}"
        )
    if len(found) > top:
        lines.append(f"  ... 그 외 {len(found) - top}개")
    return "\n".join(lines)


# -------------------------------------------------------------- old revision


def trace_at_revision(rev: str, audio: str | Path, a4_hz: float = 440.0) -> Trace:
    """Trace the same audio using another revision's `src`.

    A sparse worktree (src only — the fixtures are 59MB and not needed) gets
    the old library; *this* file is copied in on top, so a revision that
    predates the tracer can still be traced. If the old engine's API differs
    the copy fails loudly, which is the honest outcome.
    """
    root = Path(__file__).resolve().parents[3]
    audio_path = Path(audio).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="tuner-trace-"))
    worktree = tmp / "src-at-rev"

    def git(*args: str, cwd: Path = root) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        git("worktree", "add", "--no-checkout", "--detach", str(worktree), rev)
        try:
            git("sparse-checkout", "set", "src", cwd=worktree)
            git("checkout", cwd=worktree)
            shutil.copy2(Path(__file__), worktree / "src" / "tuner" / "tools" / "trace.py")
            out = tmp / "at-rev.jsonl"
            env = dict(os.environ, PYTHONPATH=str(worktree / "src"))
            subprocess.run(
                [
                    sys.executable, "-m", "tuner.tools.trace", str(audio_path),
                    "-o", str(out), "--a4", str(a4_hz), "--quiet",
                ],
                cwd=worktree, env=env, check=True,
            )
            trace = read_jsonl(out)
        finally:
            git("worktree", "remove", "--force", str(worktree))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sha = git("rev-parse", "--short", rev)
    return Trace(trace.audio, trace.sr, trace.detector, trace.a4_hz, sha, trace.frames)


# ---------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    # Korean console default is cp949; never let an unencodable
    # character turn a report into a traceback
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio", nargs="?", help="audio file, or a saved .jsonl trace")
    parser.add_argument("-o", "--out", help="write the trace to this .jsonl")
    parser.add_argument("--vs", metavar="REV", help="also trace with REV's src, then diff")
    parser.add_argument("--diff", nargs=2, metavar=("A", "B"), help="diff two saved traces")
    parser.add_argument("--a4", type=float, default=440.0)
    parser.add_argument("--detector", choices=sorted(DETECTOR_CHOICES), default="yin")
    parser.add_argument("--cents-tol", type=float, default=CENTS_TOL)
    parser.add_argument(
        "--explain", action="store_true", help="name the moments the policy acted"
    )
    parser.add_argument("--quiet", action="store_true", help="write only, print nothing")
    args = parser.parse_args(argv)

    if args.diff:
        before, after = (read_jsonl(p) for p in args.diff)
        print(diff_report(before, after, diff(before, after, args.cents_tol)))
        return 0
    if not args.audio:
        parser.error("audio file required (or --diff A B)")

    if args.audio.endswith(".jsonl"):  # a saved trace, e.g. from a field report
        trace = read_jsonl(args.audio)
    else:
        detector = DETECTOR_CHOICES[args.detector]()
        trace = trace_file(args.audio, detector=detector, a4_hz=args.a4)
    if args.out:
        write_jsonl(trace, args.out)
    if args.vs:
        before = trace_at_revision(args.vs, args.audio, a4_hz=args.a4)
        print(diff_report(before, trace, diff(before, trace, args.cents_tol)))
    elif args.explain:
        print(explain(trace))
    elif not args.quiet:
        print(summary(trace))
    return 0


if __name__ == "__main__":
    sys.exit(main())
