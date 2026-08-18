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
    parser.add_argument("audio", nargs="?", help="audio file to trace")
    parser.add_argument("-o", "--out", help="write the trace to this .jsonl")
    parser.add_argument("--vs", metavar="REV", help="also trace with REV's src, then diff")
    parser.add_argument("--diff", nargs=2, metavar=("A", "B"), help="diff two saved traces")
    parser.add_argument("--a4", type=float, default=440.0)
    parser.add_argument("--detector", choices=sorted(DETECTOR_CHOICES), default="yin")
    parser.add_argument("--cents-tol", type=float, default=CENTS_TOL)
    parser.add_argument("--quiet", action="store_true", help="write only, print nothing")
    args = parser.parse_args(argv)

    if args.diff:
        before, after = (read_jsonl(p) for p in args.diff)
        print(diff_report(before, after, diff(before, after, args.cents_tol)))
        return 0
    if not args.audio:
        parser.error("audio file required (or --diff A B)")

    detector = DETECTOR_CHOICES[args.detector]()
    trace = trace_file(args.audio, detector=detector, a4_hz=args.a4)
    if args.out:
        write_jsonl(trace, args.out)
    if args.vs:
        old = trace_at_revision(args.vs, args.audio, a4_hz=args.a4)
        print(diff_report(old, trace, diff(old, trace, args.cents_tol)))
    elif not args.quiet:
        print(summary(trace))
    return 0


if __name__ == "__main__":
    sys.exit(main())
