"""The provider picker, now the Sources tab of Settings.

Kept so imports of `ProvidersScreen` keep working: it is Settings, opened on the
Sources tab. Toggles still take effect on the next launch and the last provider
still cannot be switched off.
"""

from __future__ import annotations

from .settings import SettingsScreen, ToggleRow

ProviderItem = ToggleRow


class ProvidersScreen(SettingsScreen):
    def __init__(self) -> None:
        super().__init__("sources")


__all__ = ["ProviderItem", "ProvidersScreen"]
