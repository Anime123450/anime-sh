"""Source picker: when a show matches more than one provider entry, list them
all ("Title — provider · N eps") and let the user choose which to play from,
instead of the app guessing. A single match forwards straight to the detail
screen; no match falls back to the fan-out.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListView, LoadingIndicator

from ...domain.models import Anime
from ..widgets import SourceItem
from .detail import DetailScreen


class SourcesScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, anime: Anime, *, resume_episode: float | None = None) -> None:
        super().__init__()
        self.anime = anime
        self.resume_episode = resume_episode

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="sources-body"):
            yield Label("Choose a source", classes="section")
            yield LoadingIndicator(id="sources-loading")
            yield ListView(id="sources")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self.anime.title.preferred
        self.query_one("#sources", ListView).display = False
        self._load_sources()

    @work(exclusive=True, group="sources")
    async def _load_sources(self) -> None:
        try:
            sources = await self.app.services.playback.list_sources(self.anime)
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
