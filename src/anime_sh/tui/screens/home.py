"""Home screen: search-as-you-type, continue watching, this season, trending."""

from __future__ import annotations

from datetime import date, datetime, timezone

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListView

from ...domain.models import Season
from ..format import home_subtitle
from ..widgets import AnimeItem
from .sources import SourcesScreen


def _current_season() -> tuple[Season, int]:
    today = date.today()
    m = today.month
    if m in (12, 1, 2):
        return Season.WINTER, today.year + (1 if m == 12 else 0)
    if m in (3, 4, 5):
        return Season.SPRING, today.year
    if m in (6, 7, 8):
        return Season.SUMMER, today.year
    return Season.FALL, today.year


class HomeScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder="Search anime…  (press / to focus)", id="search")
        with VerticalScroll(id="body"):
            yield Label("Continue Watching", classes="section", id="sec-continue")
            yield ListView(id="continue")
            yield Label("Favorites", classes="section", id="sec-favorites")
            yield ListView(id="favorites")
            yield Label("Airing This Season", classes="section", id="sec-seasonal")
            yield ListView(id="seasonal")
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
        self._load_favorites()
        self._load_seasonal()
        self._load_trending()
        # Tick the airing countdowns in place every minute (no network).
        self.set_interval(60, self._tick_countdowns)
        # Focus a browse list, not the search box (the placeholder says "press /
        # to focus"). Keeps arrow-nav, Enter, and the global `?` working at once.
        try:
            self.query_one("#trending", ListView).focus()
        except Exception:
            pass

    def _tick_countdowns(self) -> None:
        for wid in ("#seasonal", "#trending", "#results"):
            try:
                lv = self.query_one(wid, ListView)
            except Exception:
                continue
            for item in lv.children:
                if isinstance(item, AnimeItem) and item.anime.is_airing:
                    item.set_subtitle(home_subtitle(item.anime))

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

    @work(exclusive=True, group="favorites")
    async def _load_favorites(self) -> None:
        try:
            items = await self.app.services.library.favorites()
        except Exception:
            items = []
        lv = self.query_one("#favorites", ListView)
        await lv.clear()
        # Empty favorites: hide the section rather than show a blank row.
        if not items:
            self.query_one("#sec-favorites").display = False
            lv.display = False
            return
        self.query_one("#sec-favorites").display = True
        lv.display = True
        for fav in items:
            meta = " · ".join(str(x) for x in (fav.anime.format.value, fav.anime.year) if x)
            lv.append(AnimeItem(fav.anime, subtitle=meta))

    @work(exclusive=True, group="seasonal")
    async def _load_seasonal(self) -> None:
        season, year = _current_season()
        try:
            animes = await self.app.services.metadata.seasonal(season, year)
        except Exception as e:
            self.notify(f"Couldn't load this season: {e}", severity="warning")
            return
        # Soonest-airing first, so the next release to drop sits at the top.
        far = datetime.max.replace(tzinfo=timezone.utc)
        animes = sorted(animes, key=lambda a: a.next_airing_at or far)
        lv = self.query_one("#seasonal", ListView)
        await lv.clear()
        for a in animes[:20]:
            lv.append(AnimeItem(a, subtitle=home_subtitle(a)))

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
            lv.append(AnimeItem(a, subtitle=home_subtitle(a)))

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
            lv.append(AnimeItem(r.anime, subtitle=home_subtitle(r.anime)))
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
        for wid in ("#sec-continue", "#continue", "#sec-favorites", "#favorites",
                    "#sec-seasonal", "#seasonal", "#sec-trending", "#trending"):
            node = self.query_one(wid)
            # Continue-watching and favorites hide themselves when empty; respect
            # that instead of forcing them back on when search results close.
            if wid in ("#sec-continue", "#continue") and not self._has_rows("#continue"):
                node.display = False
            elif wid in ("#sec-favorites", "#favorites") and not self._has_rows("#favorites"):
                node.display = False
            else:
                node.display = on

    def _has_rows(self, selector: str) -> bool:
        try:
            return len(self.query_one(selector, ListView)) > 0
        except Exception:
            return False
