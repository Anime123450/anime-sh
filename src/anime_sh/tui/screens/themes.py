"""The theme picker, now the Appearance tab of Settings.

Kept so imports of `ThemesScreen` keep working: it is Settings, opened on the
tab with the theme list. Live preview, Enter to keep and Escape to put the old
theme back all behave as they did.
"""

from __future__ import annotations

from .settings import SettingsScreen, ThemeRow

ThemeItem = ThemeRow


class ThemesScreen(SettingsScreen):
    def __init__(self) -> None:
        super().__init__("appearance")


__all__ = ["ThemeItem", "ThemesScreen"]
