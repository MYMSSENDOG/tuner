"""Turn a field report into a fixture — after checking it reproduces at all.

    python -m tuner.tools.promote ~/.tuner/reports/20260818T142211Z
    python -m tuner.tools.promote <report> --name flute_flicker_C6

A report (Ctrl+R in the app, see app/capture.py) holds the audio, the trace
the meter actually produced, and the build that produced it. Before that is
worth keeping as a fixture, one question has to be answered: does replaying
the audio through the same pipeline show the same thing?

- identical: the defect is in the code, and a fixture will hold it still.
- different: what you saw depended on timing, the device, or the block
  sizes it delivered — a fixture would never reproduce it, and the report
  itself is the evidence to keep.

Without --name nothing is copied; the answer alone is often what you wanted.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from tuner.analysis.trace import Trace, read_jsonl
from tuner.core.detector import DETECTORS
from tuner.tools.trace import aligned_diff, diff_report, shifted, summary, trace_file

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "audio"

# A replay starts cold: the tracker's smoother and the latch have no history,
# while the live capture inherited both. The first buffer's worth of readings
# can therefore differ legitimately (measured: 10 frames on a real session),
# as can the single frame at the tail where the two runs end. Judging
# reproducibility on those would call every honest report irreproducible.
DEFAULT_WARMUP_FRAMES = 16  # YIN: frame_size / hop = 4096 / 256
AUDIO_SUFFIXES = (".wav", ".flac", ".aif", ".aiff", ".ogg")


def load_report(report: Path) -> tuple[dict, Trace, Path]:
    audio = report / "audio.wav"
    trace = report / "trace.jsonl"
    for required in (audio, trace):
        if not required.exists():
            raise SystemExit(f"리포트가 아니다: {required} 없음")
    meta_path = report / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return meta, read_jsonl(trace), audio


def substantive(spans: list, total_frames: int, warmup: int = DEFAULT_WARMUP_FRAMES) -> list:
    """Divergences that are neither the cold start nor the last frame."""
    return [s for s in spans if s.start >= warmup and s.end < total_frames - 1]


def _warmup_frames(trace: Trace) -> int:
    detector = _detector_named(trace.detector)
    if detector is None:
        return DEFAULT_WARMUP_FRAMES
    return int(detector.frame_size // detector.hop_size)


def _detector_named(name: str):
    """The report says which detector was live; replay with the same one."""
    for detector_cls in DETECTORS:
        if detector_cls.name == name:
            return detector_cls()
    return None


def reproduce(report: Path) -> tuple[Trace, Trace, list]:
    """Replay the report's audio through the current pipeline and compare.

    The comparison corrects for the engine's priming gap (see
    tools/trace.py aligned_diff) — a report taken mid-stream is missing no
    audio, but its replay cannot detect anything until the ring is full.
    """
    meta, captured, audio = load_report(report)
    replayed = trace_file(
        audio,
        detector=_detector_named(captured.detector or meta.get("detector", "")),
        a4_hz=float(captured.a4_hz or meta.get("a4_hz", 440.0)),
    )
    return (captured, replayed, aligned_diff(captured, replayed)[1])


def promote(audio: Path, name: str, *, annotate: bool = True, into: Path | None = None) -> Path:
    """Copy the report's audio into the fixture corpus and label it."""
    if any(name.endswith(suffix) for suffix in AUDIO_SUFFIXES):
        name = Path(name).stem
    directory = into or FIXTURE_DIR
    target = directory / f"{name}.wav"
    if target.exists():
        raise SystemExit(f"이미 있다: {target}")
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(audio, target)
    if annotate:
        from tuner.tools.annotate import main as annotate_main

        annotate_main([str(target)])
    return target


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # cp949 consoles
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("report", type=Path, help="report directory")
    parser.add_argument("--name", help="fixture name; without it nothing is copied")
    parser.add_argument("--no-annotate", action="store_true", help="skip .ref.json")
    args = parser.parse_args(argv)

    meta, captured, audio = load_report(args.report)
    captured_note = f"현장 rev {captured.rev}"
    if meta.get("device"):
        captured_note += f", 장치 {meta['device']}"
    print(f"{args.report.name}: {captured_note}")
    print(summary(captured))
    print()

    _, replayed, spans = reproduce(args.report)
    shift, _ = aligned_diff(captured, replayed)
    if shift:
        print(
            f"정렬 보정 {shift}프레임 - 라이브는 버퍼가 찬 채 시작하고, "
            "재생은 버퍼를 채우면서 시작한다"
        )
    print(diff_report(shifted(captured, shift), replayed, spans))
    print()
    real = substantive(spans, len(replayed.frames), _warmup_frames(captured))
    if real:
        print(
            "현장과 재생이 다르다 — 타이밍·장치·블록 크기에 의존하는 현상일 수 있다.\n"
            "픽스처로 만들어도 그대로 재현되지 않는다는 뜻이니, 리포트 자체를 증거로 남길 것."
        )
    elif spans:
        print(
            "재현된다 - 다른 곳은 시작 몇 프레임(재생은 스무더·래치가 빈 채 출발한다)과 "
            "맨 끝 프레임뿐이다. 픽스처가 이 현상을 붙든다."
        )
    else:
        print("현장 표시와 오프라인 재생이 프레임 단위로 같다 - 픽스처가 이 현상을 붙든다.")

    if not args.name:
        print("\n(--name <이름> 을 주면 tests/fixtures/audio/ 로 승격한다)")
        return 0

    target = promote(audio, args.name, annotate=not args.no_annotate)
    print(f"\n승격: {target}")
    print(
        "다음: test_real_audio 가 이 파일을 자동으로 채점한다. 방 녹음이라 잡음이 많으면\n"
        "이름에 .snr 을 넣어(예: name.snr20.wav) 완화된 기준으로 채점되게 한다.\n"
        "표시 깜빡임이 주제라면 tests/integration/test_display_stability.py 의 CASES 에\n"
        "추가하고, 고치기 전에 그 테스트가 실제로 실패하는지부터 확인할 것."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
