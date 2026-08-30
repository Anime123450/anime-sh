"""The Textual TUI — the second adapter onto the app services.

Bare ``anime`` launches this. It holds no domain logic: screens call the app
services (search, catalog, library, playback) and render domain models. To keep
the layering honest (the TUI and CLI are independent siblings), this receives
services by injection from the composition root — it never imports the CLI or a
concrete infra adapter; only app services and domain ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable

from textual.app import App
from textual.binding import Binding
from textual.screen import Screen

from ..app.library import LibraryService
from ..app.playback import PlaybackService
from ..app.search import SearchService
from ..domain.ports import MetadataSource
from .screens.home import HomeScreen
from .themes import register as register_themes


@dataclass(slots=True)
class TuiServices:
    search: SearchService
    metadata: MetadataSource
    library: LibraryService
    playback: PlaybackService
    aclose: Callable[[], Awaitable[None]]
    # Optional AniList tracker — enables the My List screen when linked.
    tracker: object | None = None
    # Optional AniList sync service — lets the home screen pull remote progress
    # on launch so Continue Watching reflects other devices. None when unlinked.
    sync: object | None = None


# There used to be a map from config theme names onto Textual's built-ins. It
# existed only because every entry was the identity, and it silently swallowed
# any name not in it — so a theme this app defines itself could never be applied
# from config at all. `themes.available` is the list now, and the config value is
# just a theme name.


class AnimeShApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "anime-sh"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("l", "my_list", "My List"),
        Binding("t", "themes", "Theme"),
        # priority so `?` opens help even while the search box has focus.
        Binding("question_mark", "help", "Help", priority=True),
        Binding("escape", "back", "Back", show=False),
    ]

    # A repeated failure is one fact, not two. A rate limit hit by the seasonal
    # load and again by whatever you typed next stacked two identical toasts over
    # the list, hiding the rows behind an error you had already read.
    _REPEAT_TOAST_WINDOW_S = 8.0

    def __init__(self, services: TuiServices, *, theme: str = "tokyo-night") -> None:
        super().__init__()
        self.services = services
        self._wanted_theme = theme
        self._recent_toast: tuple[str, float] = ("", 0.0)

    def notify(self, message: str, **kwargs):  # type: ignore[override]
        """Drop a toast identical to one still on screen from moments ago."""
        last, when = self._recent_toast
        now = monotonic()
        if message == last and now - when < self._REPEAT_TOAST_WINDOW_S:
            return None
        self._recent_toast = (message, now)
        return super().notify(message, **kwargs)

    def get_default_screen(self) -> Screen:
        # HomeScreen is the base screen, so it's active as soon as the app runs.
        return HomeScreen()

    def on_mount(self) -> None:
        register_themes(self)
        if self._wanted_theme in self.available_themes:
            self.theme = self._wanted_theme
        # Playback status lines ("Episode 5/12 — trying HD-1…", "Next episode:
        # 6/12", "Skipped intro") surface as toasts.
        self.services.playback.set_on_event(self._on_playback_event)

    def _on_playback_event(self, msg: str) -> None:
        self.notify(msg, timeout=3)
        # These events also mark episode boundaries — an episode completing, the
        # auto-next advancing — so refresh the detail screen's ✓/progress marks
        # live. Without this a completion mid auto-next only shows after the
        # whole run ends (you'd have to leave and re-open the screen).
        from .screens.detail import DetailScreen

        if isinstance(self.screen, DetailScreen):
            self.screen.refresh_marks()

    async def on_unmount(self) -> None:
        self.services.playback.set_on_event(None)

    async def action_quit(self) -> None:  # type: ignore[override]
        await self.services.aclose()
        self.exit()

    def action_focus_search(self) -> None:
        try:
            self.query_one("#search").focus()
        except Exception:
            pass

    def action_help(self) -> None:
        from .screens.help import HelpScreen

        # Don't stack multiple help modals.
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())

    def action_themes(self) -> None:
        from .screens.themes import ThemesScreen

        if not isinstance(self.screen, ThemesScreen):
            self.push_screen(ThemesScreen())

    def action_my_list(self) -> None:
        from .screens.mylist import MyListScreen

        if self.services.tracker is None:
            self.notify("Link AniList first: run `anime auth login`.",
                        severity="warning")
            return
        if not isinstance(self.screen, MyListScreen):
            self.push_screen(MyListScreen())

    def action_back(self) -> None:
        if len(self.screen_stack) > 1:  # keep the base HomeScreen
            self.pop_screen()


async def run_tui(services: TuiServices, *, theme: str = "tokyo-night") -> None:
    """Entry point used by the CLI's bare ``anime`` command."""
    # Probe the terminal for a graphics protocol (Sixel/kitty) before Textual
    # takes over IO — that's the only window it works in. Enables sharp covers.
    from .coverart import prime_graphics

    prime_graphics()
    app = AnimeShApp(services, theme=theme)
    try:
        await app.run_async()
    finally:
        # Quitting with `q` closes the container itself, but every other way out —
        # Ctrl-C, a crash, the terminal going away — used to skip it, leaking HTTP
        # clients and leaving the database without a clean close. aclose is
        # idempotent, so doing it here covers all of them.
        await services.aclose()
