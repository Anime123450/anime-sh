"""Home screen: search-as-you-type, continue watching, this season, trending."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListView, Static

from ...domain.models import Season, Status
from ..format import browse_cells, continue_cells
from ..rows import Columns, columns_for, title_cells
from ..upcoming import render, schedule
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


def _schedule_is_stale(anime, now: datetime) -> bool:
    """Whether ``anime``'s cached airing schedule could have changed since it was
    stored, and so is worth a network round trip.

    Skips only what is *positively known* to be settled. ``Status`` defaults to
    ``UNKNOWN``, and a row saved bare — on playback, say — carries that default;
    reading it as "finished, nothing can change" would pin the row to a schedule
    it never had and offer an unreleased episode as though it were waiting for
    you, which is the bug the cached schedule exists to prevent.
    """
    if anime.is_airing:
        # A cached next episode still in the future is everything the row needs:
        # the countdown ticks locally, so there is nothing to fetch.
        return anime.next_airing_at is None or anime.next_airing_at <= now
    if anime.status in (Status.FINISHED, Status.CANCELLED):
        return False  # the schedule is final and will not change again
    return True  # UNKNOWN, NOT_YET_RELEASED, HIATUS — we genuinely do not know


class HomeScreen(Screen):
    # Escape is bound app-wide to "go back", which on the base screen has nothing
    # to pop and so did nothing at all — leaving no way out of a search except
    # selecting the box and deleting it by hand.
    BINDINGS = [
        Binding("escape", "clear_search", "Clear search", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_top", "Top", show=False),
        Binding("G", "cursor_bottom", "Bottom", show=False),
    ]

    def action_clear_search(self) -> None:
        box = self.query_one("#search", Input)
        if box.value:
            box.value = ""  # Input.Changed puts the browse sections back

    # Where the keyboard should land, best first. Continue Watching is what you
    # opened the app for; Trending is the fallback when the library is empty.
    _FOCUS_ORDER = ("#continue", "#favorites", "#seasonal", "#trending")

    def _adopt_focus(self) -> None:
        """Put the keyboard on the best list that has rows, once one does.

        Called after each section renders rather than at mount, because at mount
        every list is empty and focusing an empty one does nothing.

        Sections finish loading in whatever order their workers happen to return,
        so claiming the first one to arrive put focus somewhere different on
        every launch. It settles on the best *available* list instead, and will
        upgrade to a better one that arrives later — but only while the auto-
        chosen list is still the focused widget. The moment you move, or type in
        the search box, this stops touching focus at all.
        """
        if self.query_one("#search", Input).value:
            return
        if self._focus_claimed is not None and self.focused is not self._focus_claimed:
            return  # you have moved since; leave it alone

        for wid in self._FOCUS_ORDER:
            try:
                lv = self.query_one(wid, ListView)
            except Exception:
                continue
            if not (lv.display and len(lv.children)):
                continue
            if lv is self._focus_claimed:
                # Already the best available list — but Continue Watching paints
                # twice (cached rows, then enriched), and rebuilding its items
                # drops the cursor back to None. Without this the launch state
                # has a focused list and no highlighted row in it.
                if lv.index is None:
                    lv.index = 0
                return
            lv.focus()
            if lv.index is None:
                lv.index = 0  # otherwise the first arrow press selects nothing
            self._focus_claimed = lv
            return

    # -- vim motions -------------------------------------------------------- #
    # `j`/`k` and `g`/`G` are the vocabulary a terminal user reaches for first,
    # and cost nothing next to the arrow keys they sit beside. Bound on the
    # screen rather than globally so typing "j" into the search box stays typing.
    def _focused_list(self) -> ListView | None:
        node = self.focused
        return node if isinstance(node, ListView) else None

    def action_cursor_down(self) -> None:
        if (lv := self._focused_list()) is not None:
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        if (lv := self._focused_list()) is not None:
            lv.action_cursor_up()

    def action_cursor_top(self) -> None:
        if (lv := self._focused_list()) is not None and len(lv.children):
            lv.index = 0

    def action_cursor_bottom(self) -> None:
        if (lv := self._focused_list()) is not None and len(lv.children):
            lv.index = len(lv.children) - 1

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder="Search anime…  (press / to focus)", id="search")
        # Region B (the rows) and Region C (the context rail) side by side. The
        # rows cap themselves at a readable measure, so on a wide terminal they
        # stop around column 96 and leave most of the window empty; the rail is
        # what that space is for. It is hidden below 120 columns — see
        # `_size_rail` — so a small terminal is exactly as it was.
        with Horizontal(id="columns"):
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
                # Searching hides the browse sections, so a query that matches
                # nothing left the whole screen blank under a "Results" heading with
                # no indication of what had happened. This is what fills that space.
                yield Label("", id="results-empty")
            with VerticalScroll(id="rail"):
                yield Label("Coming Up", classes="section", id="sec-rail")
                yield Static("", id="rail-body")
        yield Footer()

    @property
    def _cols(self) -> Columns:
        """Column widths for the current terminal. Before the first layout the
        screen reports width 0, so fall back to a sane measure rather than
        collapsing every row to its minimum."""
        return columns_for(self.size.width or 100)

    def _cols_for(self, rows) -> Columns:
        """Columns sized to the widest title *this* list actually holds."""
        widest = max((title_cells(r) for r in rows), default=None)
        return columns_for(self.size.width or 100, widest)

    # Region C appears only when there is genuinely room for it. Below this the
    # rows alone fill the window and a rail would be stealing from them; the
    # monospace-design standard puts the same boundary at 120 columns, with the
    # region expanding again on a "wide" (160+) terminal.
    _RAIL_MIN_WIDTH = 120
    _RAIL_WIDE_WIDTH = 160

    def _size_rail(self) -> None:
        """Show, hide and size the context rail for the current terminal."""
        try:
            rail = self.query_one("#rail")
        except Exception:
            return
        width = self.size.width or 100
        rail.display = width >= self._RAIL_MIN_WIDTH
        if rail.display:
            rail.styles.width = 42 if width >= self._RAIL_WIDE_WIDTH else 34
            self._render_rail()

    def _render_rail(self) -> None:
        """Repaint the rail from the shows Continue Watching already loaded."""
        try:
            body = self.query_one("#rail-body", Static)
            rail = self.query_one("#rail")
        except Exception:
            return
        if not rail.display:
            return
        width = int(rail.styles.width.value) if rail.styles.width else 34
        days = schedule(self._upcoming_source, datetime.now(timezone.utc))
        body.update(render(days, width - 4))  # -4 for the rail's own padding
        self.query_one("#sec-rail").display = True

    def on_resize(self) -> None:
        self._size_rail()
        """Re-lay-out in place. Rebuilding the lists would be simpler and would
        also drop the user's selection every time they dragged a window edge."""
        for lv in self.query(ListView):
            items = [i for i in lv.children if isinstance(i, AnimeItem)]
            if not items:
                continue
            cols = self._cols_for([i._row for i in items])
            for item in items:
                item.relayout(cols)

    def on_mount(self) -> None:
        self._debounce = None
        self._continue_ids: set[int] = set()
        self._upcoming_source: list = []
        self._show_home_sections(True)
        self.query_one("#sec-results").display = False
        self.query_one("#results").display = False
        self.query_one("#results-empty").display = False
        self._size_rail()
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
        # to focus"). Keeps arrow-nav, Enter and the global `?` working at once.
        #
        # Deliberately *not* done here, which is what the previous version got
        # wrong: at mount every list is still empty, focusing an empty ListView
        # does not stick, and the rows arrive later from workers that clear and
        # rebuild the list. The failure was silent — a bare try/except — so the
        # app launched with focus on the search Input, where arrow keys did
        # nothing to the lists and no row was ever selected. `_adopt_focus` runs
        # once rows actually exist.
        self._focus_claimed = None

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
        self._render_rail()
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
        cols = self._cols_for([r for _, r, _ in rows])
        for anime, row, resume in rows:
            lv.append(AnimeItem(anime, row, cols, resume_episode=resume))
        self._set_section("#sec-continue", "Continue Watching", len(rows))

        # A show you are already watching does not need to be advertised again
        # further down the page. Seasonal listed four of these twice, with
        # different metadata each time, which read as two different shows.
        self._continue_ids = {a.id.anilist for a, _, _ in rows}
        # The rail is built from exactly these objects — no extra requests. See
        # tui/upcoming.py for why that matters.
        self._upcoming_source = [a for a, _, _ in rows]
        self._render_rail()
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
        """Map anilist id → freshly-fetched Anime for the rows whose airing
        schedule could actually have changed.

        This used to fetch *every* Continue-Watching row at once — twenty
        concurrent AniList queries on launch, on top of seasonal, trending and
        the AniList sync. AniList rate-limits well below that, so a normal launch
        earned a 429, and because the limiter is shared the next thing you typed
        failed too: "Search failed: rate limited — try again in about 41s".

        Almost none of those requests could return anything new:

        * a show that has finished airing has no schedule left to change;
        * a show whose cached next episode is still in the future already has
          everything the row needs — the countdown ticks locally, no network.

        What remains is the handful whose next episode has aired since the row
        was cached, and those go out a few at a time rather than all at once.
        """
        get = getattr(self.app.services.metadata, "get", None)
        if get is None:
            return {}

        now = datetime.now(timezone.utc)
        stale = [it.anime for it in items if _schedule_is_stale(it.anime, now)]
        if not stale:
            return {}

        # AniList's budget is shared with seasonal, trending and sync, all of
        # which are in flight right now. A small gate keeps this from being the
        # thing that exhausts it.
        gate = asyncio.Semaphore(4)

        async def one(anime):
            async with gate:
                try:
                    return await get(anime.id)
                except Exception:
                    return None

        results = await asyncio.gather(*(one(a) for a in stale))
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
        built = [(fav.anime, browse_cells(fav.anime)) for fav in items]
        cols = self._cols_for([r for _, r in built])
        for anime, row in built:
            lv.append(AnimeItem(anime, row, cols))
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
        built = [(a, browse_cells(a)) for a in animes[:20]]
        cols = self._cols_for([r for _, r in built])
        for a, row in built:
            lv.append(AnimeItem(a, row, cols))
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
        built = [(a, browse_cells(a)) for a in animes]
        cols = self._cols_for([r for _, r in built])
        for a, row in built:
            lv.append(AnimeItem(a, row, cols))
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
        built = [(r.anime, browse_cells(r.anime)) for r in results]
        cols = self._cols_for([row for _, row in built])
        for anime, row in built:
            lv.append(AnimeItem(anime, row, cols))
        self._toggle_results(True)
        self._show_no_matches(None if results else query)
        if results:
            lv.index = 0

    def _show_no_matches(self, query: str | None) -> None:
        """Say so when a search found nothing, instead of showing bare space.

        AniList's search is strict about word boundaries, so a near-miss really
        does come back empty — and since searching hides the browse sections,
        the result was an empty screen under a "Results" heading that gave no
        clue whether it was still loading, broken, or simply had no answer.
        """
        label = self.query_one("#results-empty", Label)
        if query is None:
            label.display = False
            return
        shown = query if len(query) <= 40 else query[:39] + "…"
        label.update(
            # Dim rather than italic: italic is not one of the four text
            # treatments this UI uses, and a fair number of terminals render it
            # as reverse video or drop it entirely.
            f"  [b]No matches for[/][dim] {shown}[/dim]\n"
            f"  [dim]Try fewer words, or a different spelling — partial titles "
            f"like [/][cyan]fri[/][dim] work.\n"
            f"  Press [/][cyan]esc[/][dim] to clear the search.[/]"
        )
        label.display = True

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
        if not on:
            # Clearing the box brings the browse sections back; the no-matches
            # notice must not outlive the search that produced it.
            self._show_no_matches(None)
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
        # Every section calls this once it has rendered its rows, which makes it
        # the one place that reliably knows a list is populated — and therefore
        # focusable. `_adopt_focus` is a no-op after the first success.
        self._adopt_focus()
