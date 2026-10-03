"""Wall 5 — two keys may not mean two things.

Shortcuts are a namespace, and nothing until now owned it. The window already
carries two families and is about to carry a third: `Ctrl+*` saves reports for
the developer, and the recorder being built gets its own modifier so the two
never look alike. A collision between them is silent — Qt resolves an
ambiguous activation by firing neither — so the meter would simply stop
responding to a key with no error anywhere.

Nothing here is a measured value. "No two bindings share a key sequence" is
true regardless of which keys get chosen; when a key moves, move it, do not
widen this.
"""

from __future__ import annotations

from collections import Counter

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence, QShortcut

from tests.fakes import FakeAudioInput
from tests.synth import tone
from tuner.app import main_window as mw


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """Every optional control switched on, so no binding hides behind a flag.

    The session-report button is off by default (it costs the controls row
    width), and its shortcut is registered with it — a test that took the
    defaults would check half the namespace.
    """
    monkeypatch.setattr(mw, "REPORT_SESSION_BUTTON_ENABLED", True)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = mw.MainWindow(FakeAudioInput(tone(440.0, 0.05)), settings)
    yield w
    w.close()


def bindings(widget) -> list[tuple[str, str]]:
    """(key sequence, what it is bound to) for every shortcut under a widget."""
    found = []
    for shortcut in widget.findChildren(QShortcut):
        target = shortcut.parent().__class__.__name__
        found.append((shortcut.key().toString(QKeySequence.SequenceFormat.PortableText), target))
    return found


def test_the_window_actually_registers_shortcuts(window):
    """Power check: an empty namespace has no collisions either."""
    keys = [key for key, _ in bindings(window)]
    assert keys, "no shortcuts found — the collision check below would be vacuous"
    assert mw.REPORT_SHORTCUT in keys
    assert mw.REPORT_SESSION_SHORTCUT in keys


def test_no_two_shortcuts_share_a_key(window):
    """Qt fires *neither* of two ambiguous shortcuts, so a collision reads as
    a key that simply stopped working — with nothing logged."""
    counts = Counter(key for key, _ in bindings(window))
    clashes = {key: n for key, n in counts.items() if n > 1}
    assert not clashes, f"same key bound more than once: {clashes}"


def test_every_shortcut_constant_is_a_real_key(window):
    """A typo'd sequence parses to an empty QKeySequence and binds nothing —
    the feature is then simply missing, with no error at startup."""
    for key, target in bindings(window):
        assert key, f"a shortcut on {target} has no key sequence"
        assert not QKeySequence(key).isEmpty()


def test_the_report_keys_stay_one_family():
    """The modifier is what tells the two recorders apart (docs/recording.md:
    the Ctrl family writes developer reports; the player-facing recorder gets
    its own). Both report keys therefore start with Ctrl, and neither is bare.

    Ctrl is also the portable half of this: Qt maps it to Command on macOS, so
    the same constant reads natively on both machines this app runs on.
    """
    for name, key in (
        ("REPORT_SHORTCUT", mw.REPORT_SHORTCUT),
        ("REPORT_SESSION_SHORTCUT", mw.REPORT_SESSION_SHORTCUT),
    ):
        assert key.startswith("Ctrl+"), f"{name} = {key!r} left the Ctrl family"
    assert mw.REPORT_SHORTCUT != mw.REPORT_SESSION_SHORTCUT


def test_no_shortcut_is_a_bare_key(window):
    """A modifier-less shortcut steals the key from whatever gains focus later
    — and on macOS a bare letter is also how you type. Every binding carries
    at least one modifier, on every platform the suite runs on."""
    for key, target in bindings(window):
        assert any(mod in key for mod in ("Ctrl+", "Alt+", "Shift+", "Meta+")), (
            f"{target} binds the bare key {key!r}"
        )


def test_a_collision_would_be_caught(window):
    """The check only means something if a duplicate fails it."""
    before = Counter(key for key, _ in bindings(window))
    duplicate = QShortcut(QKeySequence(mw.REPORT_SHORTCUT), window)
    try:
        after = Counter(key for key, _ in bindings(window))
        assert after[mw.REPORT_SHORTCUT] == before[mw.REPORT_SHORTCUT] + 1
        assert any(n > 1 for n in after.values())
    finally:
        duplicate.setParent(None)
        duplicate.deleteLater()
