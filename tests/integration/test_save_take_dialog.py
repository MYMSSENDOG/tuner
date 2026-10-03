"""The save dialog: every way of discarding asks first, and Enter never saves
an empty title.

These drive the real dialog (shown, offscreen) rather than replacing its
`exec`, because what is under test is how Qt routes the close button, Esc and
the default button — exactly the part a stub would skip.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

from tuner.app.save_take_dialog import SaveTakeDialog


@pytest.fixture
def dialog(qapp):
    d = SaveTakeDialog("2026-10-03 21-47", seconds=12.0, a4_hz=442.0)
    d.show()
    qapp.processEvents()
    yield d
    d.done(QDialog.DialogCode.Rejected)  # bypasses reject(): teardown asks nothing


def answer(monkeypatch, button):
    asked = []

    def question(*args, **kwargs):
        asked.append(args[1] if len(args) > 1 else "")
        return button

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


@pytest.mark.parametrize("how", ["close button", "escape", "discard button"])
def test_every_way_of_discarding_asks_and_can_be_refused(dialog, qapp, monkeypatch, how):
    """The close button used to discard without asking — and closing the main
    window mid-take opens this very dialog, so that was a lost performance."""
    asked = answer(monkeypatch, QMessageBox.StandardButton.Cancel)

    if how == "close button":
        dialog.close()
    elif how == "escape":
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog._discard.click()
    qapp.processEvents()

    assert asked, f"{how} discarded without asking"
    assert dialog.isVisible(), f"{how} closed the dialog although the answer was no"


@pytest.mark.parametrize("how", ["close button", "escape", "discard button"])
def test_confirmed_discard_rejects(dialog, qapp, monkeypatch, how):
    answer(monkeypatch, QMessageBox.StandardButton.Discard)
    if how == "close button":
        dialog.close()
    elif how == "escape":
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog._discard.click()
    qapp.processEvents()

    assert not dialog.isVisible()
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_enter_saves_the_default_title(dialog, qapp, monkeypatch):
    asked = answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    QTest.keyClick(dialog._field, Qt.Key.Key_Return)
    qapp.processEvents()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.title == "2026-10-03 21-47"
    assert not asked


def test_enter_on_an_empty_title_does_nothing(dialog, qapp, monkeypatch):
    """The save button waits for a title; Enter used to go around it."""
    answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    dialog._field.clear()
    QTest.keyClick(dialog._field, Qt.Key.Key_Return)
    qapp.processEvents()
    assert dialog.isVisible()
    assert dialog.result() != QDialog.DialogCode.Accepted
