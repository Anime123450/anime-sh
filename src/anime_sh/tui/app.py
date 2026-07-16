"""The Textual TUI — the second adapter onto the app services.

Bare ``anime`` launches this. It holds no domain logic: screens call the app
services (search, catalog, library, playback) and render domain models. To keep
the layering honest (the TUI and CLI are independent siblings), this receives
services by injection from the composition root — it never imports the CLI or a
concrete infra adapter; only app services and domain ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from textual.app import App
from textual.binding import Binding
from textual.screen import Screen

from ..app.library import LibraryService
from ..app.playback import PlaybackService
from ..app.search import SearchService
from ..domain.ports import MetadataSource
from .screens.home import HomeScreen


@dataclass(slots=True)
class TuiServices:
    search: SearchService
    metadata: MetadataSource
    library: LibraryService
    playback: PlaybackService
    aclose: Callable[[], Awaitable[None]]


# Map our config theme names onto Textual's built-in themes.
_THEMES = {
    "tokyo-night": "tokyo-night",
    "nord": "nord",
    "gruvbox": "gruvbox",
    "dracula": "dracula",
}


class AnimeShApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "anime-sh"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "back", "Back", show=False),
    ]

    def __init__(self, services: TuiServices, *, theme: str = "tokyo-night") -> None:
        super().__init__()
        self.services = services
        self._wanted_theme = theme

    def get_default_screen(self) -> Screen:
        # HomeScreen is the base screen, so it's active as soon as the app runs.
        return HomeScreen()

    def on_mount(self) -> None:
        if self._wanted_theme in _THEMES and _THEMES[self._wanted_theme] in self.available_themes:
            self.theme = _THEMES[self._wanted_theme]

    async def action_quit(self) -> None:  # type: ignore[override]
        await self.services.aclose()
        self.exit()

    def action_focus_search(self) -> None:
        try:
            self.query_one("#search").focus()
        except Exception:
            pass

    def action_back(self) -> None:
        if len(self.screen_stack) > 1:  # keep the base HomeScreen
            self.pop_screen()


async def run_tui(services: TuiServices, *, theme: str = "tokyo-night") -> None:
    """Entry point used by the CLI's bare ``anime`` command."""
    app = AnimeShApp(services, theme=theme)
    await app.run_async()
