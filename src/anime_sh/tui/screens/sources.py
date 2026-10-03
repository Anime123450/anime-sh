"""Source picker: when a show matches more than one provider entry, list them
all ("Title — provider · N eps") and let the user choose which to play from,
instead of the app guessing. A single match forwards straight to the detail
screen; no match falls back to the fan-out.
"""

from __future__ import annotations

import contextlib

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, ListView, LoadingIndicator

from ...domain.models import Anime, Audio
from ..shell import ActionBar, TopBar, spaced
from ..widgets import SourceItem
from .detail import DetailScreen


class SourcesScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, anime: Anime, *, resume_episode: float | None = None) -> None:
        super().__init__()
        self.anime = anime
        self.resume_episode = resume_episode

    DEFAULT_CSS = """
    SourcesScreen #sources-body { padding: 0 2; }
    """

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with VerticalScroll(id="sources-body"):
            # `shelf-label`, not `section`: the redesign renamed that class and
            # this screen kept the old name, so its one heading rendered as
            # unstyled body text — the same size and weight as the rows under it.
            yield Label(spaced("Choose a source"), classes="shelf-label")
            yield LoadingIndicator(id="sources-loading")
            yield ListView(id="sources")
        yield ActionBar(id="actionbar")

    def on_mount(self) -> None:
        self.title = self.anime.title.preferred
        with contextlib.suppress(Exception):
            width = self.size.width or 100
            self.query_one("#topbar", TopBar).render_bar(
                self.anime.title.preferred, width, hint="pick a source")
            self.query_one("#actionbar", ActionBar).render_actions(
                width, zoom=False, contextual=(("↵", "choose"), ("esc", "back")))
        self.query_one("#sources", ListView).display = False
        self._load_sources()

    @work(exclusive=True, group="sources")
    async def _load_sources(self) -> None:
        try:
            # Defaulted to SUB, so a dub viewer was offered the sub entries and
            # then played from one of them.
            sources = await self.app.services.playback.list_sources(
                self.anime, audio=getattr(self.app, "audio", Audio.SUB)
            )
        except Exception as e:
            self.notify(f"Couldn't list sources: {e}", severity="error")
            sources = []

        # 0 or 1 match → skip the picker entirely.
        if len(sources) <= 1:
            self.app.pop_screen()
            self.app.push_screen(
                DetailScreen(
                    self.anime,
                    resume_episode=self.resume_episode,
                    source=sources[0] if sources else None,
                )
            )
            return

        self.query_one("#sources-loading").display = False
        lv = self.query_one("#sources", ListView)
        lv.display = True
        for source in sources:
            lv.append(SourceItem(source))
        lv.index = 0
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SourceItem):
            self.app.pop_screen()
            self.app.push_screen(
                DetailScreen(
                    self.anime,
                    resume_episode=self.resume_episode,
                    source=item.source,
                )
            )
