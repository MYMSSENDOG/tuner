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

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import soundfile as sf

SUFFIX = ".ogg"
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
    """Collects input blocks between start and stop, minus any paused stretch.

    Audio is held in memory and written once, at stop: writing from the audio
    thread is how you get dropouts, and the same reasoning already governs
    `capture.py`. A take is one list of blocks, so pausing is simply not
    appending — the gap does not exist in the result rather than being silence.
    """

    def __init__(self) -> None:
        self._blocks: list[np.ndarray] = []
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
        """Recorded length — paused stretches were never collected, so this is
        the length of the file that will be written, not of the wall clock."""
        if not self._sr:
            return 0.0
        return sum(len(b) for b in self._blocks) / self._sr

    def start(self) -> None:
        self._blocks = []
        self._running = True
        self._paused = False

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def toggle_pause(self) -> bool:
        self._paused = not self._paused
        return self._paused

    def push_block(self, block: np.ndarray, sr: int) -> None:
        """Called on the audio thread, for every block, always. Cheap enough
        to be unconditional: one list append when running, one test when not."""
        if not self._running or self._paused:
            return
        if sr:
            self._sr = sr
        self._blocks.append(np.asarray(block, dtype=np.float32).copy())

    def take(self) -> np.ndarray:
        """Everything collected so far, as one signal."""
        if not self._blocks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._blocks)

    def stop(self) -> np.ndarray:
        signal = self.take()
        self._running = False
        self._paused = False
        self._blocks = []
        return signal

    def discard(self) -> None:
        self._running = False
        self._paused = False
        self._blocks = []


def save_take(
    signal: np.ndarray,
    sr: int,
    title: str,
    *,
    directory: Path | None = None,
    a4_hz: float = 440.0,
    recorded_at: datetime | None = None,
) -> Path:
    """Write one take, signed, and return where it landed."""
    if len(signal) == 0:
        raise RuntimeError("녹음된 소리가 없습니다")
    target = directory if directory is not None else recordings_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = unique_path(target, title)
    stamp = (recorded_at or datetime.now(UTC)).astimezone()
    with sf.SoundFile(
        str(path), "w", samplerate=int(sr), channels=1, format=FORMAT, subtype=SUBTYPE
    ) as handle:
        handle.software = SIGNATURE
        handle.date = stamp.strftime("%Y-%m-%d")
        # ASCII only, so it survives the round trip; the title is the filename
        handle.comment = json.dumps(
            {"app": SIGNATURE, "a4_hz": round(float(a4_hz), 3), "at": stamp.isoformat()},
            ensure_ascii=True,
        )
        handle.write(np.asarray(signal, dtype=np.float32))
    return path


def signature_of(path: Path) -> str | None:
    """The `software` tag, or None if the file cannot be read as audio."""
    try:
        with sf.SoundFile(str(path)) as handle:
            return handle.software
    except Exception:  # noqa: BLE001 — not audio, truncated, locked: not ours
        return None


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
