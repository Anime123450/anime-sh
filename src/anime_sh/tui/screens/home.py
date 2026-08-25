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
from ..format import browse_cells, continue_cells
from ..rows import Columns, columns_for
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

    @property
    def _cols(self) -> Columns:
        """Column widths for the current terminal. Before the first layout the
        screen reports width 0, so fall back to a sane measure rather than
        collapsing every row to its minimum."""
        return columns_for(self.size.width or 100)

    def on_resize(self) -> None:
        """Re-lay-out in place. Rebuilding the lists would be simpler and would
        also drop the user's selection every time they dragged a window edge."""
        cols = self._cols
        for lv in self.query(ListView):
            for item in lv.children:
                if isinstance(item, AnimeItem):
                    item.relayout(cols)

    def on_mount(self) -> None:
        self._debounce = None
        self._continue_ids: set[int] = set()
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

    def on_screen_suspend(self) -> None:
        # A screen (detail, sources, …) was pushed over Home.
        self._was_suspended = True

    def on_screen_resume(self) -> None:
        # Only refresh after Home was actually suspended and revealed again —
        # i.e. you went into a show and came back. Whatever you watched changed
        # the library, so rebuild the library-backed sections. Guarding on the
        # suspend avoids re-loading on the initial show (which on_mount already
        # did) — that double-load churned the exclusive workers.
        if getattr(self, "_was_suspended", False):
            self._was_suspended = False
            self._load_continue()
            self._load_favorites()

    def _tick_countdowns(self) -> None:
        for wid in ("#seasonal", "#trending", "#results"):
            try:
                lv = self.query_one(wid, ListView)
            except Exception:
                continue
            for item in lv.children:
                if isinstance(item, AnimeItem) and item.anime.is_airing:
                    fresh = browse_cells(item.anime)
                    item.set_status(fresh.status, fresh.status_cells)

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
        try:
            await self._continue_worker()
        except Exception as e:
            # An unhandled worker error takes the whole TUI down with a traceback.
            # A momentarily busy or damaged database must degrade to an empty
            # section and a message, never a crash on launch.
            self.notify(f"Couldn't load Continue Watching: {e}", severity="warning")

    async def _continue_worker(self) -> None:
        items = await self.app.services.library.continue_watching(limit=20)
        # First paint from the cached rows — a local DB read, so it's instant.
        # This is what stops Continue Watching sitting blank on launch while a
        # dozen metadata fetches run.
        await self._render_continue(items, {})
        # Then enrich with fresh airing schedules in the background and repaint —
        # that's how a caught-up airing show gets its countdown and a
        # finished-and-fully-watched show drops off.
        if items:
            fresh = await self._fresh_airing(items)
            await self._render_continue(items, fresh)

    async def _render_continue(self, items, fresh: dict) -> None:
        rows = []
        for it in items:
            anime = fresh.get(it.anime.id.anilist) or it.anime
            built = continue_cells(anime, it.progress)
            if built is None:
                continue  # finished and fully watched — nothing to continue
            row, resume = built
            rows.append((anime, row, resume))

        lv = self.query_one("#continue", ListView)
        sec = self.query_one("#sec-continue")
        await lv.clear()
        if not rows:
            sec.display = False
            lv.display = False
            return
        # Ordered by how ready each row is to be acted on: the episode you are
        # part-way through first, then unwatched episodes waiting, then the shows
        # you are caught up on, dimmed at the bottom.
        rows.sort(key=lambda r: r[1].rank)
        sec.display = True
        lv.display = True
        cols = self._cols
        for anime, row, resume in rows:
            lv.append(AnimeItem(anime, row, cols, resume_episode=resume))
        self._set_section("#sec-continue", "Continue Watching", len(rows))

        # A show you are already watching does not need to be advertised again
        # further down the page. Seasonal listed four of these twice, with
        # different metadata each time, which read as two different shows.
        self._continue_ids = {a.id.anilist for a, _, _ in rows}
        self._hide_seasonal_duplicates()

    def _hide_seasonal_duplicates(self) -> None:
        """Hide seasonal rows for shows already in Continue Watching.

        Deliberately hides rather than rebuilds: both lists are filled by
        independent workers, and clearing one from the other's worker is a
        check-then-act across an await. Setting ``display`` touches nothing the
        other worker owns.
        """
        try:
            lv = self.query_one("#seasonal", ListView)
        except Exception:
            return
        shown = 0
        for item in lv.children:
            if isinstance(item, AnimeItem):
                item.display = item.anime.id.anilist not in self._continue_ids
                shown += item.display
        self._set_section("#sec-seasonal", "Airing This Season", shown)

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
        cols = self._cols
        for fav in items:
            lv.append(AnimeItem(fav.anime, browse_cells(fav.anime), cols))
        self._set_section("#sec-favorites", "Favorites", len(items))

    @work(exclusive=True, group="seasonal")
    async def _load_seasonal(self) -> None:
        season, year = _current_season()
        lv = self.query_one("#seasonal", ListView)
        lv.loading = True  # spinner while the network call runs
        try:
            animes = await self.app.services.metadata.seasonal(season, year)
        except Exception as e:
            self.notify(f"Couldn't load this season: {e}", severity="warning")
            return
        finally:
            lv.loading = False
        # Soonest-airing first, so the next release to drop sits at the top.
        far = datetime.max.replace(tzinfo=timezone.utc)
        animes = sorted(animes, key=lambda a: a.next_airing_at or far)
        await lv.clear()
        cols = self._cols
        for a in animes[:20]:
            lv.append(AnimeItem(a, browse_cells(a), cols))
        # Counts the rows that survive de-duplication, not the fetch limit. The
        # header used to read "20" for both this and Continue Watching because
        # both had simply hit their cap — a number that looked like data.
        self._hide_seasonal_duplicates()

    @work(exclusive=True, group="trending")
    async def _load_trending(self) -> None:
        lv = self.query_one("#trending", ListView)
        lv.loading = True
        try:
            animes = await self.app.services.metadata.trending(limit=20)
        except Exception as e:
            self.notify(f"Couldn't load trending: {e}", severity="warning")
            return
        finally:
            lv.loading = False
        await lv.clear()
        cols = self._cols
        for a in animes:
            lv.append(AnimeItem(a, browse_cells(a), cols))
        self._set_section("#sec-trending", "Trending", len(animes))

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
        cols = self._cols
        for r in results:
            lv.append(AnimeItem(r.anime, browse_cells(r.anime), cols))
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

    def _set_section(self, sec_id: str, base: str, count: int) -> None:
        """Append a dim count to a section header, e.g. 'Trending  20'."""
        try:
            label = self.query_one(sec_id, Label)
            label.update(f"{base}  [dim]{count}[/dim]" if count else base)
        except Exception:
            pass
