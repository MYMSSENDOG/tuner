"""The right-click menu, styled the way current desktops draw theirs.

Windows 11 and recent macOS menus share one look: a floating rounded panel
with a hairline border, generous inner padding, and each item a rounded pill
that lights up under the pointer, with a divider setting a destructive action
apart from the rest. Qt's default menu on this dark window is a flat block
with square corners — it reads as a different program.

Rounded corners need the menu's own window to be frameless and translucent;
otherwise the corners the stylesheet leaves unpainted come out as black
squares. The colours are the drawer's, so the menu belongs to the panel it
opened from.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QWidget

PANEL = "#2b313d"
EDGE = "#4a5569"
INK = "#dce4ef"
HOVER = "#3d4a60"
SEPARATOR = "#3e4757"

STYLE = f"""
QMenu {{
    background-color: {PANEL};
    border: 1px solid {EDGE};
    border-radius: 9px;
    padding: 5px;
}}
QMenu::item {{
    color: {INK};
    padding: 6px 26px 6px 12px;
    border-radius: 5px;
    margin: 1px 0;
}}
QMenu::item:selected {{
    background-color: {HOVER};
}}
QMenu::separator {{
    height: 1px;
    background: {SEPARATOR};
    margin: 4px 6px;
}}
"""


def modern_menu(parent: QWidget | None = None) -> QMenu:
    menu = QMenu(parent)
    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    menu.setStyleSheet(STYLE)
    return menu
