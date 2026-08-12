"""Hear one audio file while several display-parameter variants track it
side by side — the tool behind docs/process/subjective-ux.md step 4.

    python -m tuner.tools.compare tests/fixtures/audio/trumpet_vib_A4.aif --loop
    python -m tuner.tools.compare file.wav --variant "아주강함:0.25:0.01"

Every pane runs the full production pipeline (own engine, tracker, latch)
on sample-identical audio; only the smoothing parameters differ. Pick with
your eyes and ears, then make the winner the default.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any

from tuner.core.tracker import PitchTracker
from tuner.tools.playback import PlaybackTap, SharedPlayback


@dataclass(frozen=True)
class Variant:
    label: str
    min_cutoff_hz: float | None  # None = smoothing off
    beta: float = 0.0
    desc: str = ""  # 이 설정이 체감상 어떻게 다른지 한 줄

    @property
    def title(self) -> str:
        if self.min_cutoff_hz is None:
            return f"{self.label} — 스무딩 없음"
        return f"{self.label} — cutoff {self.min_cutoff_hz:g}Hz · β {self.beta:g}"

    def tracker_factory(self):
        return lambda dt: PitchTracker(
            smooth_min_cutoff_hz=self.min_cutoff_hz, smooth_beta=self.beta, dt_s=dt
        )


# cutoff: 정지 상태의 스무딩 강도(낮을수록 바늘이 고요하지만 둔해짐)
# β: 변화 속도에 비례해 필터를 여는 정도(높을수록 비브라토·글리산도 통과)
DEFAULT_VARIANTS = [
    Variant("raw", None, desc="검출값 그대로 — 지터 원본, 반응 최속"),
    Variant("초약", 4.0, 0.06, desc="거의 안 누름 — raw 와 구분되는지 볼 것"),
    Variant("약함", 2.0, 0.06, desc="가벼운 안정화, 비브라토 거의 무손실"),
    Variant("현재 기본값", 1.0, 0.04, desc="채택값: 지터 절반, 비브라토 85% 보존"),
    Variant("강함", 0.5, 0.015, desc="바늘 매우 고요, 비브라토 눈에 띄게 감쇠"),
    Variant("초강", 0.25, 0.01, desc="과도 스무딩 — 비브라토가 뭉개지는 예시"),
    Variant("저β", 1.0, 0.0, desc="속도 적응 없음 — 글리산도가 굼뜨는 예시"),
    Variant("고β", 1.0, 0.12, desc="적응 과다 — 지터 억제가 약해지는 예시"),
]
MAX_PANES = 8  # 4 columns x 2 rows


@dataclass
class CompareView:
    """The assembled comparison UI plus handles the caller/tests drive it by."""

    window: Any  # QWidget
    engines: list[Any] = field(default_factory=list)
    meters: list[Any] = field(default_factory=list)
    _bridges: list[Any] = field(default_factory=list)  # keep signals alive


def build_compare_window(taps, variants: list[Variant]) -> CompareView:
    """Assemble the grid window. Separated from main() so tests can drive it
    with fake inputs."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

    from tuner.app.engine import TunerEngine
    from tuner.app.meter_widget import MeterWidget
    from tuner.app.trace_widget import PitchTraceWidget

    class _Bridge(QObject):
        reading = Signal(object)

    window = QWidget()
    window.setWindowTitle("Tuner — 변형 비교")
    window.setStyleSheet("background-color: #3b4252; color: #c7d2e3;")
    grid = QGridLayout(window)
    view = CompareView(window=window)

    for i, (tap, variant) in enumerate(zip(taps, variants)):
        pane = QVBoxLayout()
        title = QLabel(variant.title)
        title.setStyleSheet("font-weight: bold; padding: 2px;")
        desc = QLabel(variant.desc)
        desc.setStyleSheet("color: #8fa8c7; padding: 0 2px 2px;")
        desc.setWordWrap(True)
        meter = MeterWidget()
        meter.setMinimumSize(210, 210)  # 4x2 grid must fit a laptop screen
        trace = PitchTraceWidget()
        trace.setFixedHeight(56)
        pane.addWidget(title)
        pane.addWidget(desc)
        pane.addWidget(meter, stretch=1)
        pane.addWidget(trace)

        bridge = _Bridge()
        engine = TunerEngine(
            tap, bridge.reading.emit, tracker_factory=variant.tracker_factory()
        )
        engine.set_a4(442.0)

        def on_reading(reading, meter=meter, trace=trace):
            meter.set_reading(reading)
            trace.add_reading(reading)

        bridge.reading.connect(on_reading)
        grid.addLayout(pane, i // 4, i % 4)
        view.engines.append(engine)
        view.meters.append(meter)
        view._bridges.append(bridge)

    return view


def parse_variant(spec: str) -> Variant:
    parts = spec.split(":")
    label, mc, beta = [*parts, "0", "0"][:3]
    desc = parts[3] if len(parts) > 3 else ""
    if mc.lower() in ("off", "none", "raw"):
        return Variant(label, None, desc=desc)
    return Variant(label, float(mc), float(beta), desc=desc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument(
        "--variant", action="append", metavar="LABEL:CUTOFF:BETA[:설명]",
        help="반복 지정, 최대 8개 (CUTOFF 에 off 를 주면 스무딩 없음). "
        "지정이 없으면 기본 8종 (4x2 그리드)",
    )
    args = parser.parse_args(argv)
    variants = [parse_variant(s) for s in args.variant] if args.variant else DEFAULT_VARIANTS
    variants = variants[:MAX_PANES]

    from PySide6.QtWidgets import QApplication

    from tuner.app.main_window import enable_ctrl_c

    app = QApplication(sys.argv if argv is None else [sys.argv[0]])
    shared = SharedPlayback(args.audio, loop=args.loop)
    taps = [PlaybackTap(shared) for _ in variants]
    view = build_compare_window(taps, variants)
    _sigint_timer = enable_ctrl_c(view.window)
    view.window.resize(1500, 860)
    view.window.show()
    for engine in view.engines:
        engine.start()
    shared.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
