"""What the suite measured, run over run — the numbers the gates throw away.

    python -m tuner.tools.scoreboard                 # 최근 5런, 변한 지표만
    python -m tuner.tools.scoreboard --last 10 --all
    python -m tuner.tools.scoreboard --vs e661614    # 그 리비전과 최신 런 비교
    python -m tuner.tools.scoreboard --check         # 악화가 있으면 exit 1

The suite's thresholds sit just above the worst measured fixture, so a metric
can drift most of the way to its bound without failing anything. This reads
what `tests/metrics.py` recorded (one directory per run, one file per xdist
worker) and shows the drift.

It is an observation layer on purpose: `--check` is opt-in, and nothing here
is wired into the gates. Turning every number into a threshold is how a suite
starts costing more to maintain than the product it guards.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE = Path(__file__).resolve().parents[3] / "metrics"


@dataclass(frozen=True)
class Run:
    id: str  # <utc>-<rev>, so plain sorting is chronological
    rev: str
    values: dict[str, float]
    better: dict[str, str]

    @property
    def when(self) -> str:
        return self.id.split("-", 1)[0]


def read_runs(base: Path | None = None) -> list[Run]:
    """Every recorded run, oldest first. A run is a directory of worker files."""
    root = (base or DEFAULT_BASE) / "runs"
    if not root.is_dir():
        return []
    runs = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        values: dict[str, float] = {}
        better: dict[str, str] = {}
        for part in sorted(directory.glob("*.jsonl")):
            for line in part.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                values[item["name"]] = item["value"]
                better[item["name"]] = item.get("better", "lower")
        if not values:
            continue
        rev = directory.name.split("-", 1)[1] if "-" in directory.name else "?"
        runs.append(Run(directory.name, rev, values, better))
    return runs


def _select(names: list[str], pattern: str | None) -> list[str]:
    if not pattern:
        return names
    return [n for n in names if pattern in n or fnmatch.fnmatch(n, pattern)]


def _label(name: str, width: int) -> str:
    """Long metric names keep their tail — that is where the quantity is."""
    trimmed = name if len(name) <= width - 1 else "..." + name[-(width - 4) :]
    return trimmed.ljust(width)


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    magnitude = abs(value)
    if magnitude >= 100 or value == int(value):
        return f"{value:.0f}"
    if magnitude >= 10:
        return f"{value:.1f}"
    if magnitude >= 1:
        return f"{value:.2f}"
    return f"{value:.3f}"


# ------------------------------------------------------------------- table


def table(runs: list[Run], *, changed_only: bool = True, pattern: str | None = None) -> str:
    if not runs:
        return "기록된 런이 없다. 스위트를 한 번 돌리면 metrics/runs/ 에 쌓인다."
    names = sorted({name for run in runs for name in run.values})
    names = _select(names, pattern)
    rows, unchanged = [], 0
    for name in names:
        series = [run.values.get(name) for run in runs]
        seen = {v for v in series if v is not None}
        if changed_only and len(seen) <= 1:
            unchanged += 1
            continue
        rows.append((name, series))

    width = min(max((len(n) for n, _ in rows), default=10) + 1, 52)
    header = "지표".ljust(width) + "".join(f"{run.rev:>12}" for run in runs)
    lines = [
        f"런 {len(runs)}개 (오래된 것부터): " + ", ".join(f"{r.when} {r.rev}" for r in runs),
        "",
        header,
        "-" * len(header),
    ]
    for name, series in rows:
        lines.append(_label(name, width) + "".join(f"{_fmt(v):>12}" for v in series))
    if not rows:
        lines.append("(변한 지표 없음)")
    if unchanged:
        lines.append("")
        lines.append(f"변화 없는 지표 {unchanged}개는 숨김 (--all 로 표시)")
    return "\n".join(lines)


# ----------------------------------------------------------------- compare


@dataclass(frozen=True)
class Change:
    name: str
    before: float
    after: float
    better: str

    @property
    def delta(self) -> float:
        return self.after - self.before

    @property
    def pct(self) -> float | None:
        return None if self.before == 0 else 100.0 * self.delta / abs(self.before)

    @property
    def worse(self) -> bool:
        return self.delta > 0 if self.better == "lower" else self.delta < 0


def compare(before: Run, after: Run, pattern: str | None = None) -> list[Change]:
    """Metrics both runs measured, worst regression first."""
    shared = _select(sorted(set(before.values) & set(after.values)), pattern)
    changes = [
        Change(name, before.values[name], after.values[name], after.better.get(name, "lower"))
        for name in shared
        if before.values[name] != after.values[name]
    ]
    return sorted(changes, key=lambda c: (not c.worse, -abs(c.pct or 0.0)))


def regressions(changes: list[Change], tolerance: float) -> list[Change]:
    """Worse by more than `tolerance` (relative), or worse from a zero base."""
    return [
        c for c in changes if c.worse and (c.pct is None or abs(c.pct) > tolerance * 100.0)
    ]


def compare_report(before: Run, after: Run, changes: list[Change], top: int = 25) -> str:
    lines = [
        f"A {before.when} {before.rev}  ->  B {after.when} {after.rev}",
        (
            f"  공통 지표 {len(set(before.values) & set(after.values))}개 중 "
            f"{len(changes)}개 변함, 악화 {sum(1 for c in changes if c.worse)}개"
        ),
    ]
    only_after = sorted(set(after.values) - set(before.values))
    only_before = sorted(set(before.values) - set(after.values))
    if only_after:
        lines.append(f"  새 지표 {len(only_after)}개: {', '.join(only_after[:4])}")
    if only_before:
        lines.append(f"  사라진 지표 {len(only_before)}개: {', '.join(only_before[:4])}")
    if not changes:
        lines.append("  변화 없음.")
        return "\n".join(lines)
    width = min(max(len(c.name) for c in changes) + 1, 52)
    lines.append("")
    lines.append("  " + "지표".ljust(width) + f"{'A':>10}{'B':>10}{'변화':>12}")
    for change in changes[:top]:
        pct = "" if change.pct is None else f" ({change.pct:+.1f}%)"
        mark = "악화" if change.worse else "개선"
        lines.append(
            "  "
            + _label(change.name, width)
            + f"{_fmt(change.before):>10}{_fmt(change.after):>10}"
            + f"{_fmt(change.delta):>8}{pct} {mark}"
        )
    if len(changes) > top:
        lines.append(f"  ... 그 외 {len(changes) - top}개")
    return "\n".join(lines)


def pick(runs: list[Run], selector: str | None, default_index: int) -> Run:
    """Select a run by rev/id substring, or fall back to a position."""
    if selector:
        matches = [r for r in runs if selector in r.rev or selector in r.id]
        if not matches:
            raise SystemExit(f"'{selector}' 에 맞는 런이 없다 ({len(runs)}개 기록됨)")
        return matches[-1]
    return runs[default_index]


# --------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # cp949 consoles
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_BASE, help="metrics directory")
    parser.add_argument("--last", type=int, default=5, help="how many runs to show")
    parser.add_argument("--all", action="store_true", help="show unchanged metrics too")
    parser.add_argument("--filter", metavar="PATTERN", help="substring or glob on metric names")
    parser.add_argument("--vs", metavar="REV", help="compare the newest run against this one")
    parser.add_argument("--check", action="store_true", help="exit 1 on a regression")
    parser.add_argument("--tolerance", type=float, default=0.05, help="ignore drift under this")
    args = parser.parse_args(argv)

    runs = read_runs(args.dir)
    if not runs:
        print(table(runs))
        return 0
    if args.vs or args.check:
        if len(runs) < 2 and not args.vs:
            print("비교할 런이 하나뿐이다.")
            return 0
        before = pick(runs, args.vs, -2)
        after = runs[-1]
        if before.id == after.id:
            print("비교 대상이 최신 런과 같다.")
            return 0
        changes = compare(before, after, args.filter)
        print(compare_report(before, after, changes))
        if args.check:
            bad = regressions(changes, args.tolerance)
            print()
            if bad:
                print(f"{args.tolerance:.0%} 넘게 악화된 지표 {len(bad)}개:")
                for change in bad:
                    print(f"  {change.name}: {_fmt(change.before)} -> {_fmt(change.after)}")
                return 1
            print(f"{args.tolerance:.0%} 넘는 악화 없음.")
        return 0
    print(table(runs[-args.last :], changed_only=not args.all, pattern=args.filter))
    return 0


if __name__ == "__main__":
    sys.exit(main())
