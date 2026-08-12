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
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
import soundfile as sf

from tuner.audio.input import BlockCallback, InputDevice
from tuner.core.tracker import PitchTracker

BLOCK_SIZE = 256


@dataclass(frozen=True)
class Variant:
    label: str
    min_cutoff_hz: float | None  # None = smoothing off
    beta: float = 0.0

    @property
    def title(self) -> str:
        if self.min_cutoff_hz is None:
            return f"{self.label} — 스무딩 없음"
        return f"{self.label} — cutoff {self.min_cutoff_hz:g}Hz · β {self.beta:g}"

    def tracker_factory(self):
        return lambda dt: PitchTracker(
            smooth_min_cutoff_hz=self.min_cutoff_hz, smooth_beta=self.beta, dt_s=dt
        )


DEFAULT_VARIANTS = [
    Variant("raw", None),
    Variant("약함", 2.0, 0.06),
    Variant("현재 기본값", 1.0, 0.04),
    Variant("강함", 0.5, 0.015),
]


class SharedPlayback:
    """One output stream, many analysis taps: every pane hears and analyses
    the exact same blocks."""

    def __init__(self, path: str, loop: bool = False):
        signal, sr = sf.read(path, always_2d=True)
        self._signal = np.ascontiguousarray(signal.mean(axis=1), dtype=np.float32)
        self.sr = sr
        self._loop = loop
        self._pos = 0
        self._taps: list[BlockCallback] = []
        self._stream: sd.OutputStream | None = None

    def add_tap(self, callback: BlockCallback) -> None:
        self._taps.append(callback)

    def start(self) -> None:
        if self._stream is not None:
            return

        def on_block(outdata: np.ndarray, frames: int, time, status) -> None:
            chunk = self._signal[self._pos : self._pos + frames]
            self._pos += frames
            if len(chunk) < frames:
                if self._loop and len(self._signal) > 0:
                    self._pos = frames - len(chunk)
                    chunk = np.concatenate([chunk, self._signal[: self._pos]])
                else:
                    chunk = np.concatenate(
                        [chunk, np.zeros(frames - len(chunk), dtype=np.float32)]
                    )
            outdata[:, 0] = chunk
            block = chunk.astype(np.float64)
            for tap in self._taps:
                tap(block)

        self._stream = sd.OutputStream(
            samplerate=self.sr, channels=1, blocksize=BLOCK_SIZE, callback=on_block
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class PlaybackTap:
    """AudioInput view of SharedPlayback for one engine."""

    def __init__(self, shared: SharedPlayback):
        self._shared = shared

    def list_devices(self) -> list[InputDevice]:
        return []

    def start(self, device_id: int | None, callback: BlockCallback) -> int:
        # registration only — main() opens the stream once after every pane
        # is wired, so no tap misses the beginning and the tap list never
        # mutates while the audio callback iterates it
        self._shared.add_tap(callback)
        return self._shared.sr

    def stop(self) -> None:
        self._shared.stop()


def build_compare_window(taps, variants: list[Variant]):
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
    window._engines, window._meters, window._bridges = [], [], []

    for i, (tap, variant) in enumerate(zip(taps, variants)):
        pane = QVBoxLayout()
        title = QLabel(variant.title)
        title.setStyleSheet("font-weight: bold; padding: 2px;")
        meter = MeterWidget()
        meter.setMinimumSize(300, 300)
        trace = PitchTraceWidget()
        pane.addWidget(title)
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
        grid.addLayout(pane, i // 2, i % 2)
        window._engines.append(engine)
        window._meters.append(meter)
        window._bridges.append(bridge)

    return window


def parse_variant(spec: str) -> Variant:
    label, mc, beta = (spec.split(":") + ["0"])[:3]
    if mc.lower() in ("off", "none", "raw"):
        return Variant(label, None)
    return Variant(label, float(mc), float(beta))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("audio")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument(
        "--variant", action="append", metavar="LABEL:CUTOFF:BETA",
        help="쉼표로 최대 4개 반복 지정 (CUTOFF 에 off 를 주면 스무딩 없음). "
        "지정이 없으면 기본 4종 (raw/약함/현재/강함)",
    )
    args = parser.parse_args(argv)
    variants = [parse_variant(s) for s in args.variant] if args.variant else DEFAULT_VARIANTS
    variants = variants[:4]

    from PySide6.QtWidgets import QApplication

    from tuner.app.main_window import enable_ctrl_c

    app = QApplication(sys.argv if argv is None else [sys.argv[0]])
    shared = SharedPlayback(args.audio, loop=args.loop)
    taps = [PlaybackTap(shared) for _ in variants]
    window = build_compare_window(taps, variants)
    _sigint_timer = enable_ctrl_c(window)
    window.resize(880, 900)
    window.show()
    for engine in window._engines:
        engine.start()
    shared.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
