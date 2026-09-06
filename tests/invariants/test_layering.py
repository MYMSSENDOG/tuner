"""Wall 1 — the dependency rule, enforced instead of merely written down.

docs/ARCHITECTURE.md calls "no cycles, no upward edges" this codebase's first
principle and every review's first question, and until now nothing checked it.
It is the cheapest wall in the suite to hold up: the answer depends on no
audio, no threshold and no fixture, so no retuning can ever legitimately break
these tests. A failure here is a structural mistake, not a number that drifted.

Nothing in this file is a measured value. Do not relax an assertion to let a
new import pass — move the import, or change ARCHITECTURE.md first.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PKG = SRC / "tuner"

# docs/ARCHITECTURE.md "의존 규칙": arrows point down, always. Each layer may
# import itself and everything below it — never anything above.
ALLOWED_LAYERS = {
    "core": {"core"},
    "audio": {"audio"},
    "analysis": {"core", "analysis"},
    "app": {"core", "audio", "analysis", "app"},
    "tools": {"core", "audio", "analysis", "app", "tools"},
    # __main__ is the shell that assembles the app; it sits at tools' level
    "shell": {"core", "audio", "analysis", "app", "tools"},
}

# Third-party dependencies are a layer property too: core is pure DSP, so a
# frame of numpy is all it gets. `tools` is the assembly floor and takes
# whatever it needs.
ALLOWED_THIRD_PARTY = {
    "core": {"numpy"},
    "audio": {"numpy", "sounddevice"},
    "analysis": {"numpy"},
    # soundfile: app/capture.py writes the field report's wav next to its
    # trace. It is a file format, not a device — nothing about it reaches the
    # real-time path.
    "app": {"numpy", "PySide6", "soundfile"},
    "tools": None,  # anything
    "shell": None,
}

# The device library itself, as opposed to our Protocol over it. Confining it
# to these three is what keeps every layer above it drivable by a fake input.
SOUNDDEVICE_MODULES = {
    "tuner.audio.sounddevice_input",
    "tuner.audio.sounddevice_output",
    "tuner.tools.playback",
}

# Documented as Qt-free (docs/ARCHITECTURE.md layer map): the pipeline, the
# meter's arithmetic, the recorder and the metronome assembly are plain Python
# so that the suite — and tools/trace.py — can run them without a QApplication.
QT_FREE_MODULES = {
    "tuner.app.engine",
    "tuner.app.capture",
    "tuner.app.meter_model",
    "tuner.app.metronome",
}

# docs/ARCHITECTURE.md "향후 방향": the metronome is a sibling feature, not a
# part of the tuner. The tuner is handed an InterferenceSource and knows
# nothing about what makes the sound; only the window hosting both sees both.
TUNER_SIDE_MODULES = {
    "tuner.core.detector",
    "tuner.core.pitch",
    "tuner.core.spectral",
    "tuner.core.tracker",
    "tuner.core.notes",
    "tuner.core.interference",
    "tuner.app.engine",
    "tuner.app.capture",
    "tuner.app.meter_model",
    "tuner.app.meter_widget",
    "tuner.app.trace_widget",
    "tuner.app.level_widget",
}


def module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def layer_of(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in ALLOWED_LAYERS else "shell"


def _is_type_checking(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def import_nodes(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    """Every import that exists at runtime.

    `if TYPE_CHECKING:` bodies are skipped on purpose: they are annotations,
    not edges, and this codebase deliberately uses one so that app/capture.py
    and app/engine.py can name each other's types without importing each other
    (engine.py: "the capture imports this module, so keep the edge one-way").
    Counting them would report a cycle that does not exist at runtime — and
    skipping them means these tests also pin that edge as type-only forever.

    Function-level imports do count: tools/compare.py defers its Qt imports to
    keep --help fast, and a deferred import is still a dependency.
    """
    found: list[ast.Import | ast.ImportFrom] = []

    def walk(nodes: Sequence[ast.AST]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import | ast.ImportFrom):
                found.append(node)
            elif isinstance(node, ast.If) and _is_type_checking(node):
                walk(node.orelse)  # the else branch does run
            else:
                walk(list(ast.iter_child_nodes(node)))

    walk(list(ast.iter_child_nodes(tree)))
    return found


def imported_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Dotted names this statement depends on.

    `from tuner.core import pitch` names both a package and a submodule. Which
    one it is decides nothing about layering but everything about the edge in
    the module graph, so both candidates come back and the caller keeps
    whichever turns out to be a real module.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:  # relative import: this codebase uses none
        return []
    base = node.module or ""
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


def source_files() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


MODULES: dict[str, ast.Module] = {
    module_name(p): ast.parse(p.read_text(encoding="utf-8"), str(p)) for p in source_files()
}


def internal_edges(module: str) -> set[str]:
    """Runtime dependencies of `module` on other modules of this package."""
    return {
        name
        for node in import_nodes(MODULES[module])
        for name in imported_names(node)
        if name in MODULES and name != module
    }


def third_party_roots(module: str) -> set[str]:
    return {
        root
        for node in import_nodes(MODULES[module])
        for name in imported_names(node)
        if (root := name.split(".")[0]) not in ("", "tuner")
        and root not in sys.stdlib_module_names
    }


# --------------------------------------------------------------------- rules


def test_every_source_file_is_parsed():
    """A path typo would run every rule below over nothing at all, and an
    edge reader that finds nothing would pass every module vacuously."""
    assert len(MODULES) == len(source_files()) >= 25
    assert {"tuner.core.pitch", "tuner.app.engine"} <= set(MODULES)
    assert sum(len(internal_edges(m)) for m in MODULES) >= 20
    assert "tuner.core.detector" in internal_edges("tuner.app.engine")


@pytest.mark.parametrize("module", sorted(MODULES))
def test_dependencies_point_downward(module):
    allowed = ALLOWED_LAYERS[layer_of(module)]
    for target in sorted(internal_edges(module)):
        assert layer_of(target) in allowed, (
            f"{module} ({layer_of(module)}) imports {target} ({layer_of(target)}) — "
            f"{layer_of(module)} may only reach {sorted(allowed)}"
        )


@pytest.mark.parametrize("module", sorted(MODULES))
def test_third_party_stays_in_its_layer(module):
    allowed = ALLOWED_THIRD_PARTY[layer_of(module)]
    if allowed is None:
        return
    extra = third_party_roots(module) - allowed
    assert not extra, (
        f"{module} imports {sorted(extra)}; {layer_of(module)} may use {sorted(allowed)}"
    )


def test_no_runtime_import_cycles():
    """The other half of the first principle. The failure names the path that
    closes the cycle, because the edge to move is somewhere on it."""
    graph = {module: internal_edges(module) for module in MODULES}
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        path.append(node)
        for target in sorted(graph[node]):
            if state.get(target) == 1:
                return [*path[path.index(target) :], target]
            if state.get(target) is None and (cycle := visit(target)):
                return cycle
        path.pop()
        state[node] = 2
        return None

    for module in sorted(graph):
        if state.get(module) is None and (cycle := visit(module)):
            pytest.fail("import cycle: " + " -> ".join(cycle))


def test_the_device_library_lives_only_behind_the_audio_protocols():
    """Every layer above is drivable by a fake because of this one."""
    assert {m for m in MODULES if "sounddevice" in third_party_roots(m)} == SOUNDDEVICE_MODULES


@pytest.mark.parametrize("module", sorted(QT_FREE_MODULES))
def test_the_qt_free_modules_stay_qt_free(module):
    assert "PySide6" not in third_party_roots(module)
    # a widget import would drag Qt in through the back door
    assert not [t for t in internal_edges(module) if t.endswith("_widget")]


@pytest.mark.parametrize("module", sorted(TUNER_SIDE_MODULES))
def test_the_tuner_does_not_know_about_the_metronome(module):
    """The sibling seam: the tuner hears about clicks through
    core/interference.py and never about the thing that makes them."""
    for target in internal_edges(module):
        assert "metronome" not in target, f"{module} imports {target}"


def test_the_rules_would_catch_a_violation():
    """Power check (docs/process/regression.md): a reader that returns nothing
    passes everything. These are the three shapes the rules exist for."""
    upward = ast.parse("from tuner.app.engine import TunerEngine")
    assert {n for node in import_nodes(upward) for n in imported_names(node) if n in MODULES} == {
        "tuner.app.engine"
    }

    framework = ast.parse("import numpy as np\nfrom PySide6.QtWidgets import QWidget")
    roots = {
        root
        for node in import_nodes(framework)
        for name in imported_names(node)
        if (root := name.split(".")[0]) not in sys.stdlib_module_names
    }
    assert roots == {"numpy", "PySide6"}
    assert roots - ALLOWED_THIRD_PARTY["core"] == {"PySide6"}

    hidden = ast.parse(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from tuner.app.capture import X\n"
    )
    assert not [n for node in import_nodes(hidden) for n in imported_names(node) if n in MODULES]
