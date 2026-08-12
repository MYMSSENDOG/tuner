"""Every tool CLI must at least import and answer --help.

The tools are not exercised by the app path, so a broken import or a
signature drift in any of them would otherwise only surface when a human
next reaches for the tool.
"""

import pytest

from tuner.tools import (
    add_noise,
    annotate,
    build_note_bank,
    compare,
    demo,
    import_tinysol,
)

ALL_TOOLS = [annotate, add_noise, build_note_bank, compare, demo, import_tinysol]


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.__name__.split(".")[-1])
def test_help_exits_cleanly(tool, capsys):
    with pytest.raises(SystemExit) as excinfo:
        tool.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()
