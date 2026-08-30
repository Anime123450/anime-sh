"""My List screen: the user's AniList list, grouped by status.

Fetched live from the tracker on mount. Selecting an entry goes through the same
source picker as everywhere else, so playing from your list is one keystroke.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListView, LoadingIndicator

from ..rows import Row, columns_for, title_target
from ..widgets import AnimeItem
from .sources import SourcesScreen

# Friendly status → display order + label.
_ORDER = ["CURRENT", "REPEATING", "PAUSED", "PLANNING", "COMPLETED", "DROPPED"]
_LABEL = {
    "CURRENT": "Watching", "REPEATING": "Rewatching", "PAUSED": "Paused",
    "PLANNING": "Planning", "COMPLETED": "Completed", "DROPPED": "Dropped",
}


class MyListScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="mylist-body"):
            yield Label("My List", classes="section")
            yield LoadingIndicator(id="mylist-loading")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "My List"
        self._load()

    @work(exclusive=True, group="mylist")
    async def _load(self) -> None:
        try:
            entries = await self.app.services.tracker.fetch_list()
        except Exception as e:
            self.notify(f"Couldn't load your list: {e}", severity="error")
            entries = []
        try:
            self.query_one("#mylist-loading").remove()
        except Exception:
            pass
        body = self.query_one("#mylist-body", VerticalScroll)
        if not entries:
            await body.mount(Label("[dim]Your list is empty.[/dim]"))
            return
        groups: dict[str, list] = {}
        for e in entries:
            groups.setdefault(e.status, []).append(e)
        order = [s for s in _ORDER if s in groups] + [
            s for s in groups if s not in _ORDER
        ]
        for status in order:
            rows = groups[status]
            await body.mount(
                Label(f"{_LABEL.get(status, status.title())}  ({len(rows)})",
                      classes="section")
            )
            lv = ListView()
            await body.mount(lv)
            built = []
            for e in rows:
                total = e.anime.episode_count
                prog = f"{e.progress}/{total}" if total else f"{e.progress}"
                built.append((e, Row(
                    title=e.anime.title.preferred,
                    position=prog,
                    status=f"[yellow]★ {e.score:g}[/yellow]" if e.score else "",
                    status_cells=len(f"★ {e.score:g}") if e.score else 0,
                )))
            # Sized to the titles this group actually holds, like every other
            # list on the home screen. Without it a group of short names reserved
            # the whole measure and left the score column marooned to the right.
            cols = columns_for(self.size.width or 100,
                               title_target([r for _, r in built]))
            for e, row in built:
                lv.append(AnimeItem(
                    e.anime,
                    row,
                    cols,
                ))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, AnimeItem):
            self.app.push_screen(SourcesScreen(item.anime))
