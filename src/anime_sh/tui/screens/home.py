"""Home screen: search-as-you-type, continue watching, this season, trending."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListView

from ...domain.models import Season
from ..format import continue_row, home_subtitle
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
        # If AniList is linked, pull remote progress so Continue Watching
        # reflects what you watched on another device (phone, web).
        self._auto_sync()
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

    # -- AniList sync ------------------------------------------------------- #
    @work(exclusive=True, group="autosync")
    async def _auto_sync(self) -> None:
        """Pull the linked AniList list on launch so Continue Watching reflects
        progress made on other devices. Best-effort: no linked account, or any
        failure, leaves the local rows exactly as they were — never an error."""
        services = self.app.services
        sync = getattr(services, "sync", None)
        if sync is None or getattr(services, "tracker", None) is None:
            return
        try:
            result = await sync.pull()
        except Exception:
            return
        if result.pulled:
            self.notify(f"Synced {result.pulled} from AniList", timeout=3)
            # Remote progress may have advanced a show or added a new one — rebuild
            # the rows that read from the library.
            self._load_continue()
            self._load_favorites()

    # -- home data ---------------------------------------------------------- #
    @work(exclusive=True, group="continue")
    async def _load_continue(self) -> None:
        items = await self.app.services.library.continue_watching(limit=20)
        lv = self.query_one("#continue", ListView)
        await lv.clear()

        # The cached row carries no airing schedule, so enrich each show with
        # fresh AniList metadata (already cached, best-effort) — that's how a
        # caught-up airing show gets its countdown and a finished-and-watched
        # show gets dropped.
        fresh = await self._fresh_airing(items) if items else {}
        rows = []
        for it in items:
            anime = fresh.get(it.anime.id.anilist) or it.anime
            built = continue_row(anime, it.progress)
            if built is None:
                continue  # finished and fully watched — nothing to continue
            subtitle, dim, resume = built
            rows.append((anime, subtitle, dim, resume))

        if not rows:
            self.query_one("#sec-continue").display = False
            lv.display = False
            return

        # Shows you can actually watch float to the top; the ones you're caught
        # up on (waiting for the next episode) sink to the bottom, greyed.
        rows.sort(key=lambda r: r[2])
        # This worker owns the section's visibility — set it *on* here (mirrors
        # favorites). Without this it stays hidden, because on_mount hid it
        # before any rows existed.
        self.query_one("#sec-continue").display = True
        lv.display = True
        for anime, subtitle, dim, resume in rows:
            lv.append(AnimeItem(anime, subtitle=subtitle, resume_episode=resume, dim=dim))

    async def _fresh_airing(self, items) -> dict:
        """Map anilist id → freshly-fetched Anime (with airing schedule) for the
        continue-watching shows. Best-effort: a source without ``get`` or any
        fetch failure just leaves that show on its cached row."""
        get = getattr(self.app.services.metadata, "get", None)
        if get is None:
            return {}

        async def one(anime):
            try:
                return await get(anime.id)
            except Exception:
                return None

        results = await asyncio.gather(*(one(it.anime) for it in items))
        return {a.id.anilist: a for a in results if a is not None}

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
            # Cancel any search already in flight too — otherwise a request for
            # a half-typed query lands *after* the box is cleared and slams stale
            # results back over the home screen.
            self.workers.cancel_group(self, "search")
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
        # The box may have been cleared or edited while this request was in
        # flight; if it no longer matches, drop the result rather than flashing
        # stale matches over whatever the user is looking at now.
        if self.query_one("#search", Input).value.strip() != query:
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
