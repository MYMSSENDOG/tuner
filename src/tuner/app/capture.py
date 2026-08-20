"""The last N seconds of input, kept so a glitch can be reported afterwards.

The defects that are left are the ones a fixture corpus cannot contain: a
name that flickered once, on this instrument, in this room. By the time the
player notices, the sound is gone — and the process this repo works by
(docs/process/regression.md) wants a reproducing test *before* the fix.

So the engine writes every block into a ring buffer and every reading into a
trace, and one key in the app freezes the last few seconds to disk. A report
holds the audio, what the meter actually showed, and which build showed it;
`python -m tuner.tools.promote` turns that into a fixture.

Two ways to take one:

- **ring** (Ctrl+R): the last RING_SECONDS, saved *after* the fact. For the
  moment you did not see coming.
- **session** (the 기록 button): start to stop, nothing dropped. For sitting
  down and playing the thing on purpose.

Both write the same report format, so promote/trace read them the same way.
Cost when nobody is capturing: nothing — the engine takes `capture=None` and
skips both calls.
"""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from tuner.analysis.trace import Trace, TraceFrame, code_revision, write_jsonl

if TYPE_CHECKING:  # only for the annotation: capture must not import the engine
    from tuner.app.engine import TunerReading

RING_SECONDS = 10.0  # long enough to hold a phrase, short enough to save instantly

# A session is held in memory until it is saved (writing from the audio thread
# is how you get dropouts). float32 at 44.1kHz is ~10.6MB per minute, so this
# cap keeps the worst case around 100MB; the UI stops recording when it is hit.
MAX_SESSION_SECONDS = 600.0


def reports_dir() -> Path:
    """Where reports land. The env var keeps tests out of the real one."""
    override = os.environ.get("TUNER_REPORTS_DIR")
    return Path(override) if override else Path.home() / ".tuner" / "reports"


def _retimed(frames: list[TraceFrame], start_s: float) -> list[TraceFrame]:
    """Renumber and re-clock frames so the saved window starts at zero."""
    return [
        TraceFrame(
            i=i,
            t_s=round(frame.t_s - start_s, 6),
            raw_hz=frame.raw_hz,
            confidence=frame.confidence,
            hz=frame.hz,
            label=frame.label,
            cents=frame.cents,
            state=frame.state,
        )
        for i, frame in enumerate(frames)
    ]


class FieldCapture:
    """A rolling window of input audio plus the trace the meter produced."""

    def __init__(self, seconds: float = RING_SECONDS):
        self._seconds = seconds
        self._sr = 0
        self._ring = np.zeros(0)
        self._pos = 0  # next write position
        self._filled = 0  # how much of the ring holds real audio
        self._pushed = 0  # samples seen since start: the clock for frame times
        self._frames: deque[TraceFrame] = deque()
        # session recording (button-driven), independent of the ring
        self._recording = False
        self._session_blocks: list[np.ndarray] = []
        self._session_frames: list[TraceFrame] = []
        self._session_start = 0  # sample index the recording began at
        self.interrupted = False  # the stream restarted mid-recording

    @property
    def sample_rate(self) -> int:
        return self._sr

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def recorded_seconds(self) -> float:
        if not self._sr:
            return 0.0
        return sum(len(b) for b in self._session_blocks) / self._sr

    def start(self, sr: int) -> None:
        """(Re)size for a stream. A *re*start ends a running recording — the
        clock its frames are timed against restarts with it — but the first
        stream is not a restart: arming the button before any audio arrives
        has to keep recording once it does."""
        if self._recording and self._ring.size:
            self._recording = False
            self.interrupted = True
        self._sr = sr
        self._ring = np.zeros(max(int(self._seconds * sr), 1))
        self._pos = self._filled = self._pushed = 0
        # readings arrive one detector hop apart (>=256 samples); 64 is a
        # generous floor, and stale frames are dropped by time on the way out
        self._frames = deque(maxlen=int(self._seconds * sr / 64) + 8)

    def push_block(self, block: np.ndarray, sr: int) -> None:
        """Audio-thread hot path: two slice assignments, no allocation."""
        if sr != self._sr or self._ring.size == 0:
            self.start(sr)
        size = self._ring.size
        if len(block) >= size:  # a block longer than the ring: keep its tail
            self._ring[:] = block[-size:]
            self._pos, self._filled = 0, size
        else:
            end = self._pos + len(block)
            if end <= size:
                self._ring[self._pos : end] = block
            else:
                split = size - self._pos
                self._ring[self._pos :] = block[:split]
                self._ring[: len(block) - split] = block[split:]
            self._pos = end % size
            self._filled = min(self._filled + len(block), size)
        self._pushed += len(block)
        if self._recording:
            # float32: half the memory, and the report is written as float
            # anyway. copy() because the engine reuses its callback buffer.
            self._session_blocks.append(block.astype(np.float32))

    def push_reading(
        self, reading: TunerReading, raw_hz: float | None, confidence: float
    ) -> None:
        """The three numbers of one reading: what was detected, what the
        tracker decided to display, and what the latch named it."""
        note = reading.note
        frame = TraceFrame(
            i=len(self._frames),  # renumbered when the window is cut
            t_s=round(self._pushed / self._sr, 6) if self._sr else 0.0,
            raw_hz=None if raw_hz is None else round(raw_hz, 4),
            confidence=round(confidence, 4),
            hz=None if note is None else round(note.freq_hz, 4),
            label=None if note is None else note.label,
            cents=None if note is None else round(note.cents, 3),
            state=reading.state.value,
        )
        self._frames.append(frame)
        if self._recording:
            self._session_frames.append(frame)

    # --------------------------------------------------------------- session

    def start_recording(self) -> None:
        """Keep everything from now until save_recording()."""
        self._session_blocks = []
        self._session_frames = []
        self._session_start = self._pushed
        self.interrupted = False
        self._recording = True

    def cancel_recording(self) -> None:
        self._recording = False
        self._session_blocks = []
        self._session_frames = []

    def save_recording(self, **kwargs) -> Path:
        """Stop recording and write the whole session as one report."""
        self._recording = False
        if not self._session_blocks:
            raise RuntimeError("기록된 오디오가 없다 (입력이 오기 전에 정지했다)")
        signal = np.concatenate(self._session_blocks).astype(np.float64)
        frames = _retimed(self._session_frames, self._session_start / self._sr)
        kwargs.setdefault("extra", {})
        kwargs["extra"] = {
            "kind": "session",
            "interrupted": self.interrupted,
            **kwargs["extra"],
        }
        path = self._write_report(signal, self._sr, frames, **kwargs)
        self._session_blocks = []
        self._session_frames = []
        return path

    # ------------------------------------------------------------------ ring

    def snapshot(self) -> tuple[np.ndarray, int, list[TraceFrame]] | None:
        """The window as a plain signal plus frames retimed to its start."""
        if self._filled == 0 or self._sr == 0:
            return None
        if self._filled < self._ring.size:
            signal = self._ring[: self._filled].copy()
        else:
            signal = np.concatenate([self._ring[self._pos :], self._ring[: self._pos]])
        start_s = (self._pushed - self._filled) / self._sr
        frames = _retimed([f for f in self._frames if f.t_s >= start_s], start_s)
        return signal, self._sr, frames

    def save(self, **kwargs) -> Path:
        """Freeze the ring into its own directory. Raises if nothing is held."""
        snapshot = self.snapshot()
        if snapshot is None:
            raise RuntimeError("아직 캡처된 오디오가 없다 (엔진이 시작되기 전)")
        signal, sr, frames = snapshot
        kwargs.setdefault("extra", {})
        kwargs["extra"] = {"kind": "ring", **kwargs["extra"]}
        return self._write_report(signal, sr, frames, **kwargs)

    # ---------------------------------------------------------------- report

    def _write_report(
        self,
        signal: np.ndarray,
        sr: int,
        frames: list[TraceFrame],
        *,
        detector: str = "",
        a4_hz: float = 440.0,
        extra: dict | None = None,
        into: Path | None = None,
    ) -> Path:
        import soundfile as sf

        base = into or reports_dir()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        directory = base / stamp
        suffix = 1
        while directory.exists():  # two reports in the same second
            suffix += 1
            directory = base / f"{stamp}-{suffix}"
        directory.mkdir(parents=True)

        # float, not the WAV default PCM_16: a report is meant to be replayed
        # through the same pipeline, and quantizing it first breaks that
        sf.write(directory / "audio.wav", signal, sr, subtype="FLOAT")
        rev = code_revision()
        write_jsonl(
            Trace("audio.wav", sr, detector, a4_hz, rev, frames), directory / "trace.jsonl"
        )
        meta = {
            "utc": stamp,
            "rev": rev,
            "sr": sr,
            "seconds": round(len(signal) / sr, 3),
            "detector": detector,
            "a4_hz": a4_hz,
            "frames": len(frames),
        }
        meta.update(extra or {})
        (directory / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return directory
