"""Recording a practice take, and finding the ones recorded before.

This is the player-facing recorder, not the developer-facing one. `capture.py`
freezes audio *plus what the meter showed* into `~/.tuner/reports/` so a defect
can become a fixture; this writes the performance itself, somewhere a person
can find it. The two are kept apart on purpose, down to the shortcut modifier
(docs/recording.md).

Three decisions live here:

- **Where.** The music folder, not a dotfolder. These are the player's own
  takes — things to mail to a teacher or back up — and hiding them would be
  wrong. The cost is that people put other files there too, which is what the
  signature below is for.
- **How.** Streamed to disk while it is played, by a writer thread; see
  `Recorder`. Memory does not grow with the length of a take.
- **What format.** OGG Vorbis, mono. Lossless was not asked for and costs 7x
  (10 minutes: 53MB as WAV, ~7MB here). libsndfile already ships the encoder,
  so this adds no dependency.
- **How we know it is ours.** Every file we write carries `SOFTWARE=tuner` in
  its Vorbis comments, and the list only shows files that do. Copy an album
  into the folder and nothing changes on screen.

Qt-free: the window drives it, the tests drive it without a QApplication. The
one exception is locating the music folder, which asks Qt because a person may
have moved it — see `recordings_dir`.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf

SUFFIX = ".ogg"
# A take being written. Not `.ogg`, so a half-written file is never listed.
PART_SUFFIX = ".part"
# How much of the most recent audio the writer keeps as-is, to cover what the
# Vorbis encoder is still holding back when a paused take is listened to.
# Measured: 3s written, 2.80s decodable from the still-open file - the
# encoder's look-ahead is a fraction of a second. Five seconds is the margin.
TAIL_SECONDS = 5.0
# How long a preview waits for the writer to catch up with the last blocks.
CATCH_UP_SECONDS = 2.0
FORMAT = "OGG"
SUBTYPE = "VORBIS"

# What marks a file as ours. libsndfile appends its own version to whatever is
# written here ("tuner (libsndfile-1.2.2)"), so the check is a prefix, not an
# equality — a detail that cost one confused reading to find.
SIGNATURE = "tuner"

# Vorbis comments came back mangled for non-ASCII (a Korean title written as
# `title` read back as mojibake), so the title is carried by the *filename*,
# which the filesystem stores correctly on both platforms, and the tags hold
# only ASCII. The comment is JSON so it can gain fields without a new format.
ENV_DIR = "TUNER_RECORDINGS_DIR"

# Windows forbids these outright; the rest would make a path mean something
# else. A take called "G# / A♭ 연습" should still save.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_TITLE_CHARS = 80  # long enough for a real title, short of path limits

DEFAULT_TITLE_FORMAT = "%Y-%m-%d %H-%M"


def recordings_dir() -> Path:
    """Where takes live: <music folder>/Tuner.

    Qt is asked for the music folder rather than assuming `~/Music`, because
    people move it (another drive, a synced folder) and a recorder that writes
    somewhere they do not look is a recorder they will not trust. Without Qt —
    tests, a headless tool — the conventional path is correct anyway.
    """
    override = os.environ.get(ENV_DIR)
    if override:
        return Path(override)
    music = ""
    try:
        from PySide6.QtCore import QStandardPaths

        music = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MusicLocation)
    except Exception:  # noqa: BLE001 — no Qt, or no such location: fall back
        music = ""
    return (Path(music) if music else Path.home() / "Music") / "Tuner"


def default_title(now: datetime | None = None) -> str:
    """The title the save dialog opens with. Date and time, because that is
    the one thing true of every take before anyone names it."""
    return (now or datetime.now().astimezone()).strftime(DEFAULT_TITLE_FORMAT)


def safe_title(title: str) -> str:
    """A title a filesystem will accept, without changing what it says.

    Characters that cannot be in a filename become spaces rather than being
    dropped, so "A/B" stays two words instead of becoming "AB".
    """
    cleaned = _UNSAFE.sub(" ", title).strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:MAX_TITLE_CHARS].strip() or default_title()


def unique_path(directory: Path, title: str) -> Path:
    """`<title>.ogg`, or `<title> (2).ogg` if that name is taken.

    Two takes can legitimately want the same name — the default title has a
    one-minute resolution — and silently overwriting a performance is the one
    thing this must never do.
    """
    stem = safe_title(title)
    candidate = directory / f"{stem}{SUFFIX}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({n}){SUFFIX}"
        n += 1
    return candidate


@dataclass(frozen=True)
class Take:
    path: Path
    title: str
    seconds: float
    recorded_at: datetime | None = None

    @property
    def duration_text(self) -> str:
        whole = int(self.seconds)
        return f"{whole // 60}:{whole % 60:02d}"

    @property
    def when_text(self) -> str:
        """The line under the title: a date and a wall-clock time."""
        if self.recorded_at is None:
            return ""
        stamp = self.recorded_at
        hour = stamp.hour
        half = "오전" if hour < 12 else "오후"
        twelve = hour % 12 or 12
        return f"{stamp:%Y-%m-%d} {half} {twelve}:{stamp:%M}"


def group_label(recorded_at: datetime | None, now: datetime | None = None) -> str:
    """Which heading a take sits under.

    Recent takes are the ones people look for, so the near past is named in
    the words someone would use out loud and only the distant past falls back
    to a date.
    """
    if recorded_at is None:
        return "날짜 없음"
    today = (now or datetime.now().astimezone()).date()
    day = recorded_at.date()
    days = (today - day).days
    if days <= 0:
        return "오늘"
    if days == 1:
        return "어제"
    if days < 7:
        return f"{days}일 전"
    if days < 28:
        return f"{days // 7}주 전"
    if days < 365:
        return f"{days // 30}개월 전"
    return f"{day:%Y년}"


def grouped(takes: list[Take], now: datetime | None = None) -> list[tuple[str, list[Take]]]:
    """`takes` split into (heading, takes) runs, order preserved."""
    groups: list[tuple[str, list[Take]]] = []
    for take in takes:
        label = group_label(take.recorded_at, now)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(take)
        else:
            groups.append((label, [take]))
    return groups


class Recorder:
    """Streams a take to disk while it is being played.

    The audio thread only hands blocks over; a writer thread of the take's own
    encodes them into a hidden `.part` file next to where the take will end up.
    Stopping drains that thread and closes the file; saving is then a rename,
    and discarding is a delete.

    It used to hold the whole take in memory and encode it at stop. That had
    no ceiling (float32 at 44.1kHz is ~10.6MB a minute, an hour ~635MB, and
    stop briefly doubled it to join the blocks), froze the window for as long
    as the encode took, and dropped any block the audio thread appended
    between joining and clearing. Streaming removes all three at the root:
    memory is a queue a few blocks deep, there is nothing left to encode at
    stop, and everything handed over before stop is written.

    Pausing is still simply not handing blocks over — the gap does not exist
    in the result rather than being silence.

    The audio thread never touches the file. Writing from the audio callback
    is how dropouts happen; a queue put is a list append and a lock.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self._queue: queue.SimpleQueue | None = None
        self._writer: threading.Thread | None = None
        self._part: Path | None = None
        self._error: BaseException | None = None
        self._pushed = 0  # frames handed over, for the live clock
        self._written = 0  # frames the writer actually wrote
        # the newest frames the writer wrote, kept raw (see `so_far`)
        self._tail: collections.deque[np.ndarray] = collections.deque()
        self._tail_frames = 0
        self._sr = 0
        self._running = False
        self._paused = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def sample_rate(self) -> int:
        return self._sr

    @property
    def seconds(self) -> float:
        """Recorded length. Paused stretches were never handed over, so this
        is the length of the file, not of the wall clock. While running it
        counts what was handed over; once stopped, what was written."""
        if not self._sr:
            return 0.0
        frames = self._pushed if self._running else self._written
        return frames / self._sr

    @property
    def has_take(self) -> bool:
        """A stopped take with sound in it, waiting to be saved or discarded."""
        return not self._running and self._part is not None and self._written > 0

    def start(self, a4_hz: float = 440.0, directory: Path | None = None) -> None:
        """Begin a take. One in progress, or one stopped but not yet saved, is
        thrown away — starting again means the last attempt was not the one."""
        self.discard()
        target = directory or self._directory or recordings_dir()
        target.mkdir(parents=True, exist_ok=True)
        self._part = target / f".take-{uuid.uuid4().hex}{PART_SUFFIX}"
        self._error = None
        self._pushed = 0
        self._written = 0
        self._tail = collections.deque()
        self._tail_frames = 0
        self._sr = 0
        self._paused = False
        # A queue per take: a block the audio thread hands over in the instant
        # after stop lands in the old queue, never at the head of the next take.
        self._queue = queue.SimpleQueue()
        self._writer = threading.Thread(
            target=self._write,
            args=(self._queue, self._part, float(a4_hz), datetime.now(UTC).astimezone()),
            name="take-writer",
            daemon=True,
        )
        self._writer.start()
        self._running = True

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def toggle_pause(self) -> bool:
        self._paused = not self._paused
        return self._paused

    def push_block(self, block: np.ndarray, sr: int) -> None:
        """Called on the audio thread, for every block, always. One test when
        idle; a copy and a queue put when recording. The copy matters: the
        audio layer reuses its buffers."""
        target = self._queue
        if not self._running or self._paused or target is None:
            return
        if sr:
            self._sr = sr
        self._pushed += len(block)
        target.put((np.array(block, dtype=np.float32), self._sr))

    def so_far(self) -> np.ndarray:
        """Everything recorded until now, for listening back while paused.

        The take is streaming into a file that is still open, and that file
        can be read — but not all of it: the Vorbis encoder holds the last
        fraction of a second back until the stream ends, and no flush releases
        it (measured: 3.00s written, 2.80s readable). Played from the file
        alone, the last note would be cut off.

        So the file supplies what it can, and the writer's raw tail supplies
        exactly the frames the encoder is still holding: the writer knows how
        many it wrote, the file says how many it can give, and the difference
        comes from the tail. Nothing is re-encoded and nothing is held beyond a
        few seconds, however long the take.

        Only while paused: during recording the answer would be stale before it
        was returned. Raises RuntimeError when there is nothing to give.
        """
        if not (self._running and self._paused):
            raise RuntimeError("일시정지 중에만 들을 수 있습니다")
        if not self._catch_up():
            raise RuntimeError("아직 쓰는 중입니다 — 잠시 뒤에 다시")
        if self._written == 0 or self._part is None:
            raise RuntimeError("아직 녹음된 소리가 없습니다")
        try:
            decoded, _ = sf.read(str(self._part), dtype="float32")
        except Exception:  # noqa: BLE001 — no complete page yet: the tail has it all
            decoded = np.zeros(0, dtype=np.float32)
        decoded = decoded[: self._written]
        missing = self._written - len(decoded)
        if missing > self._tail_frames:
            raise RuntimeError("아직 들을 수 없습니다 — 잠시 뒤에 다시")
        if missing == 0:
            return decoded
        tail = np.concatenate(list(self._tail))
        return np.concatenate([decoded, tail[len(tail) - missing :]])

    def _catch_up(self) -> bool:
        """Wait until the writer has written every block handed over."""
        deadline = time.monotonic() + CATCH_UP_SECONDS
        while self._written < self._pushed:
            if self._error is not None or time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    def _keep_tail(self, block: np.ndarray, sr: int) -> None:
        """Writer thread only. Keeps the newest TAIL_SECONDS of what it wrote."""
        self._tail.append(block)
        self._tail_frames += len(block)
        limit = int(TAIL_SECONDS * sr)
        while self._tail and self._tail_frames - len(self._tail[0]) >= limit:
            self._tail_frames -= len(self._tail.popleft())

    def stop(self) -> float:
        """Stop collecting, write out what is queued, close the file.

        Returns the recorded length in seconds. Raises RuntimeError if the
        writer failed (disk full, folder gone) — the caller has to be told
        rather than shown a take that is not there.
        """
        if not self._running:
            return self.seconds
        self._running = False
        self._paused = False
        self._finish_writer()
        if self._error is not None:
            error, self._error = self._error, None
            self._remove_part()
            raise RuntimeError(f"녹음을 쓰지 못했습니다: {error}") from error
        return self.seconds

    def finish(self, title: str) -> Path:
        """Keep the stopped take under `title`; returns where it landed."""
        if self._running:
            self.stop()
        if not self.has_take:
            self._remove_part()
            raise RuntimeError("녹음된 소리가 없습니다")
        assert self._part is not None
        path = unique_path(self._part.parent, title)
        self._part.replace(path)
        self._part = None
        return path

    def discard(self) -> None:
        """Throw away whatever is running or waiting to be saved."""
        if self._running:
            self._running = False
            self._paused = False
            self._finish_writer()
            self._error = None
        self._remove_part()

    # --- the writer side ------------------------------------------------------

    def _finish_writer(self) -> None:
        if self._queue is not None:
            self._queue.put(None)
        if self._writer is not None:
            self._writer.join()
        self._queue = None
        self._writer = None

    def _remove_part(self) -> None:
        if self._part is not None:
            self._part.unlink(missing_ok=True)
            self._part = None

    def _write(self, source: queue.SimpleQueue, path: Path, a4_hz: float, at: datetime) -> None:
        """The writer thread. Opens the file on the first block, because only
        then is the sample rate known for certain."""
        handle = None
        try:
            while True:
                item = source.get()
                if item is None:
                    break
                block, sr = item
                if handle is None:
                    handle = open_signed(path, sr, a4_hz, at)
                handle.write(block)
                self._keep_tail(block, sr)
                self._written += len(block)
        except BaseException as error:  # noqa: BLE001 — reported by stop()
            self._error = error
            while source.get() is not None:  # drain so stop() can finish
                pass
        finally:
            if handle is not None:
                handle.close()


def open_signed(path: Path, sr: int, a4_hz: float, at: datetime) -> sf.SoundFile:
    """Open a take for writing, already carrying our signature.

    Tags have to be set before the first sample is written, which is why this
    is one step: a take file is never, even briefly, an unsigned one.
    """
    handle = sf.SoundFile(
        str(path), "w", samplerate=int(sr), channels=1, format=FORMAT, subtype=SUBTYPE
    )
    handle.software = SIGNATURE
    handle.date = at.strftime("%Y-%m-%d")
    # ASCII only, so it survives the round trip; the title is the filename
    handle.comment = json.dumps(
        {"app": SIGNATURE, "a4_hz": round(float(a4_hz), 3), "at": at.isoformat()},
        ensure_ascii=True,
    )
    return handle


def save_take(
    signal: np.ndarray,
    sr: int,
    title: str,
    *,
    directory: Path | None = None,
    a4_hz: float = 440.0,
    recorded_at: datetime | None = None,
) -> Path:
    """Write a whole signal as one signed take. The recorder streams instead;
    this is for audio that already exists in memory (tests, tools)."""
    if len(signal) == 0:
        raise RuntimeError("녹음된 소리가 없습니다")
    target = directory if directory is not None else recordings_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = unique_path(target, title)
    stamp = (recorded_at or datetime.now(UTC)).astimezone()
    with open_signed(path, sr, a4_hz, stamp) as handle:
        handle.write(np.asarray(signal, dtype=np.float32))
    return path


def signature_of(path: Path) -> str | None:
    """The `software` tag, or None if the file cannot be read as audio."""
    try:
        with sf.SoundFile(str(path)) as handle:
            return handle.software
    except Exception:  # noqa: BLE001 — not audio, truncated, locked: not ours
        return None


def take_a4(path: Path, default: float = 440.0) -> float:
    """The reference pitch the take was recorded against, from its signature.

    A take is judged against the A4 it was played to, not whatever the tuner
    happens to be set to when it is heard back - otherwise retuning the app
    would make an old take look out of tune.
    """
    try:
        with sf.SoundFile(str(path)) as handle:
            return float(json.loads(handle.comment).get("a4_hz", default))
    except Exception:  # noqa: BLE001 - unsigned, unreadable or no comment: the default
        return default


def is_ours(path: Path) -> bool:
    """Did this program write this file?

    The folder is somewhere a person browses, so membership cannot be the test
    — only the signature can. Anything else in there stays invisible to us.
    """
    signature = signature_of(path)
    return signature is not None and signature.startswith(SIGNATURE)


def take_info(path: Path) -> Take | None:
    try:
        info = sf.info(str(path))
    except Exception:  # noqa: BLE001 — unreadable files simply are not takes
        return None
    return Take(
        path=path,
        title=path.stem,
        seconds=float(info.duration),
        recorded_at=datetime.fromtimestamp(path.stat().st_mtime).astimezone(),
    )


def list_takes(directory: Path | None = None) -> list[Take]:
    """Our takes, newest first. Files we did not write are not listed."""
    target = directory if directory is not None else recordings_dir()
    if not target.is_dir():
        return []
    found = []
    for path in sorted(target.glob(f"*{SUFFIX}")):
        if not is_ours(path):
            continue
        info = take_info(path)
        if info is not None:
            found.append((path.stat().st_mtime, info))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [info for _, info in found]


def total_bytes(takes: list[Take]) -> int:
    return sum(take.path.stat().st_size for take in takes if take.path.exists())


def human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def rename_take(path: Path, new_title: str) -> Path:
    """The title is the filename, so renaming is renaming."""
    target = unique_path(path.parent, new_title)
    if target == path:
        return path
    path.rename(target)
    return target
