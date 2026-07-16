"""Home screen: search-as-you-type, continue watching, and trending."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListView

from ..widgets import AnimeItem
from .sources import SourcesScreen


class HomeScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder="Search anime…  (press / to focus)", id="search")
        with VerticalScroll(id="body"):
            yield Label("Continue Watching", classes="section", id="sec-continue")
            yield ListView(id="continue")
            yield Label("Trending", classes="section", id="sec-trending")
            yield ListView(id="trending")
            yield Label("Results", classes="section", id="sec-results")
            yield ListView(id="results")
        yield Footer()

    def on_mount(self) -> None:
        self._debounce = None
        self._show_home_sections(True)
        self.query_one("#sec-results").display = False
        self.query_one("#results").display = False
        self._load_continue()
        self._load_trending()

    # -- home data ---------------------------------------------------------- #
    @work(exclusive=True, group="continue")
    async def _load_continue(self) -> None:
        items = await self.app.services.library.continue_watching(limit=10)
        lv = self.query_one("#continue", ListView)
        await lv.clear()
        if not items:
            self.query_one("#sec-continue").display = False
            lv.display = False
            return
        for it in items:
            pct = round(it.progress.fraction * 100)
            lv.append(
                AnimeItem(it.anime, subtitle=f"Ep {it.progress.episode:g} · {pct}%",
                          resume_episode=it.progress.episode)
            )

    @work(exclusive=True, group="trending")
    async def _load_trending(self) -> None:
        try:
            animes = await self.app.services.metadata.trending(limit=20)
        except Exception as e:
            self.notify(f"Couldn't load trending: {e}", severity="warning")
            return
        lv = self.query_one("#trending", ListView)
        await lv.clear()
        for a in animes:
            year = f"· {a.year}" if a.year else ""
            lv.append(AnimeItem(a, subtitle=f"{a.format.value} {year}".strip()))

    # -- search ------------------------------------------------------------- #
    def on_input_changed(self, event: Input.Changed) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        query = event.value.strip()
        if not query:
            self._toggle_results(False)
            return
        self._debounce = self.set_timer(0.3, lambda: self._run_search(query))

    @work(exclusive=True, group="search")
    async def _run_search(self, query: str) -> None:
        try:
            results = await self.app.services.search.search(query, limit=25)
        except Exception as e:
            self.notify(f"Search failed: {e}", severity="error")
            return
        lv = self.query_one("#results", ListView)
        await lv.clear()
        for r in results:
            a = r.anime
            meta = " · ".join(str(x) for x in (a.format.value, a.year) if x)
            lv.append(AnimeItem(a, subtitle=meta))
        self._toggle_results(True)
        if results:
            lv.index = 0

    # -- navigation --------------------------------------------------------- #
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, AnimeItem):
            # Go through the source picker; it forwards straight to the detail
            # screen when there's only one match.
            self.app.push_screen(
                SourcesScreen(item.anime, resume_episode=item.resume_episode)
            )

    # -- helpers ------------------------------------------------------------ #
    def _toggle_results(self, on: bool) -> None:
        self.query_one("#sec-results").display = on
        self.query_one("#results").display = on
        self._show_home_sections(not on)

    def _show_home_sections(self, on: bool) -> None:
        for wid in ("#sec-continue", "#continue", "#sec-trending", "#trending"):
            node = self.query_one(wid)
            # Continue-watching may have been hidden for being empty; respect that.
            if wid in ("#sec-continue", "#continue") and not self._has_continue():
                node.display = False
            else:
                node.display = on

    def _has_continue(self) -> bool:
        try:
            return len(self.query_one("#continue", ListView)) > 0
        except Exception:
            return False
