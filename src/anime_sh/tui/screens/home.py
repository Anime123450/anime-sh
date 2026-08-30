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
from ..format import RANK_WAITING, browse_cells, continue_cells
from ..rows import (
    CHROME,
    Columns,
    columns_for_space,
    title_cells,
    title_target_from,
)
from ..upcoming import render, schedule, scheduled_ids
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
        return columns_for_space(self._row_space())

    def _cols_for(self, rows, key: str) -> Columns:
        """Columns for a list about to be filled, on the screen's shared grid.

        Deliberately *not* sized to this list alone. Every section sizing itself
        put Continue Watching's episode column at column 76 and Seasonal's at
        70, with Trending somewhere else again — three grids stacked down one
        screen, so the eye had no vertical line to follow and the whole thing
        read as output rather than as a layout. `rows` only contributes to the
        shared target; it never sets it on its own.
        """
        # Keyed by list so a section that reloads replaces its own contribution
        # instead of piling a second copy onto the sample.
        self._title_widths[key] = [title_cells(r) for r in rows]
        # Deliberately does NOT update `self._grid`. That is `_apply_grid`'s to
        # set, and it decides whether to move the other lists by comparing
        # against it — assign it here and the comparison always finds itself
        # equal, so the lists already on screen never follow the new grid.
        return columns_for_space(self._row_space(), self._grid_target())

    def _row_space(self) -> int:
        """Cells a row may actually occupy, measured from a mounted row.

        Asked of the widget, not computed from a constant. The paddings between
        the screen edge and a row's text all live in `app.tcss`, the scrollbar
        comes and goes with the content, and `CHROME` was six cells wrong at 100
        columns — rows overflowed their label, Textual wrapped them, `height: 1`
        hid the overflow, and the last column silently vanished.

        Before the first row exists there is nothing to measure, so the estimate
        stands in; the first `_apply_grid` after they mount corrects it.
        """
        widths = [
            item.content_region.width
            for item in self.query(AnimeItem)
            if item.content_region.width > 0
        ]
        # The narrowest, not the first. Lists do not all get the same room: a
        # section long enough to scroll gives up two columns to its scrollbar
        # and a short one does not, so Continue Watching measured 84 while
        # Seasonal measured 86. One grid spans both, so it has to fit the
        # tighter of them or the longer list silently clips its last column.
        return min(widths) if widths else self._body_width() - CHROME

    def _grid_target(self) -> int | None:
        """The title width every list on this screen is cut to."""
        return title_target_from(
            [w for group in self._title_widths.values() for w in group]
        )

    def _apply_grid(self) -> None:
        """Re-cut every list to the shared grid.

        Lists arrive from independent workers, so the sample the grid is drawn
        from grows as they land: Continue Watching alone gives one answer,
        Continue Watching plus Seasonal another. Whenever the answer changes,
        every list already on screen has to move to the new one — otherwise the
        first list to arrive keeps the grid it was born with and the alignment
        this exists to create never happens.
        """
        cols = columns_for_space(self._row_space(), self._grid_target())
        if cols == getattr(self, "_grid", None):
            return
        self._grid = cols
        for item in self.query(AnimeItem):
            item.relayout(cols)
        # Measuring is one step behind laying out: the rows this pass just
        # re-cut may have been sized against a list that had not yet grown its
        # scrollbar, so run once more against what is now on screen. This
        # terminates — a row's width comes from its list, never from the text
        # inside it, so the second pass measures the same space, computes the
        # same columns, and returns at the check above.
        self.call_after_refresh(self._apply_grid)

    def _body_width(self) -> int:
        """Cells available to Region B, the rail's share already taken out.

        Used only where nothing is mounted yet to measure — see `_row_space`.
        The rows were once sized against the whole terminal while living in a
        column the rail had already shortened, which at 120 columns produced
        96-cell rows inside a 78-cell body.
        """
        width = self.size.width or 100
        return width - (self._rail_base(width) if width >= self._RAIL_MIN_WIDTH else 0)

    # Region C appears only when there is genuinely room for it. Below this the
    # rows alone fill the window and a rail would be stealing from them; the
    # monospace-design standard puts the same boundary at 120 columns.
    _RAIL_MIN_WIDTH = 120
    _RAIL_MIN_RAIL = 34
    _RAIL_MAX_RAIL = 72
    _RAIL_SHARE = 0.33

    def _rail_base(self, width: int) -> int:
        """Region C's share of a ``width``-cell terminal before any leftover.

        A proportion, not the old two fixed steps of 34 and 42. Those stopped
        growing at 160 columns, so on a 200-column terminal the rail ellipsized
        every single title at 27 characters while 54 columns sat empty between
        it and the rows — both regions truncating on either side of a void.
        """
        share = round(width * self._RAIL_SHARE)
        return max(self._RAIL_MIN_RAIL, min(self._RAIL_MAX_RAIL, share))

    def _rail_width(self, width: int) -> int:
        """Region C's width on a ``width``-cell terminal.

        Deliberately a function of the terminal alone. An earlier version also
        absorbed whatever Region B left unused, which read better but closed a
        loop the moment row widths began being *measured* rather than computed:
        a wider rail makes a narrower body, which makes a narrower measured row,
        which leaves more spare, which widens the rail again.

        It costs less than it sounds. The shared grid and the raised measure cap
        let the rows use the width themselves, so on a 200-column terminal the
        leftover is a handful of cells rather than the 54 that started this.
        """
        return self._rail_base(width)

    def _size_rail(self) -> None:
        """Show, hide and size the context rail for the current terminal."""
        try:
            rail = self.query_one("#rail")
        except Exception:
            return
        width = self.size.width or 100
        rail.display = width >= self._RAIL_MIN_WIDTH
        if rail.display:
            rail.styles.width = self._rail_width(width)
            self._render_rail()

    def _rail_showing(self) -> bool:
        """Whether Region C is on screen. Asked of the width rather than of the
        widget's `display`, because the first Continue Watching paint can land
        before `_size_rail` has run and a `display` of False would then be read
        as "no rail" on a terminal that is about to have one."""
        return (self.size.width or 100) >= self._RAIL_MIN_WIDTH

    def _without_rail_duplicates(self, rows):
        """Drop the Continue Watching rows the rail has taken over.

        A *waiting* row is a show you are caught up on: there is nothing to
        play, and the row exists only to carry a countdown to the next episode.
        That is exactly what the rail says, grouped by day and easier to read —
        so every one of these rows was on screen twice at once. Six of six,
        measured against the real library.

        Dimming them was already an admission that they are not actionable.
        Once something else says the same thing better, the honest move is to
        stop saying it here, and give Continue Watching back to the rows you can
        press Enter on.

        Only rows the rail is *genuinely* showing are dropped — see
        `scheduled_ids`. On a narrow terminal there is no rail, and a show
        beyond the rail's horizon never reaches it; in both cases the dimmed row
        is the only place that countdown exists, so it stays.
        """
        if not self._rail_showing():
            return rows
        on_rail = scheduled_ids(
            schedule(self._upcoming_source, datetime.now(timezone.utc))
        )
        if not on_rail:
            return rows
        return [
            (anime, row, resume)
            for anime, row, resume in rows
            if row.rank != RANK_WAITING or anime.id.anilist not in on_rail
        ]

    def _render_rail(self) -> None:
        """Repaint the rail from the shows Continue Watching already loaded."""
        try:
            body = self.query_one("#rail-body", Static)
            rail = self.query_one("#rail")
        except Exception:
            return
        if not rail.display:
            return
        width = (int(rail.styles.width.value) if rail.styles.width
                 else self._rail_width(self.size.width or 100))
        days = schedule(self._upcoming_source, datetime.now(timezone.utc))
        body.update(render(days, width - 4))  # -4 for the rail's own padding
        self.query_one("#sec-rail").display = True

    def on_resize(self) -> None:
        was_showing = getattr(self, "_rail_was_showing", None)
        self._size_rail()
        now_showing = self._rail_showing()
        self._rail_was_showing = now_showing

        # Re-lay-out in place. Rebuilding the lists would be simpler and would
        # also drop the user's selection every time they dragged a window edge.
        # One re-cut for the whole screen, not one per list — the grid is shared.
        self._apply_grid()

        # Whether the rail is present decides whether Continue Watching hides its
        # waiting rows, so crossing that threshold is the one resize that has to
        # rebuild — a relayout only re-measures the rows already there, and would
        # leave a narrowed terminal with no rail *and* no countdowns. Every other
        # list is relaid out above first, so this rebuild is the only work the
        # crossing costs.
        if was_showing is not None and was_showing != now_showing:
            self._load_continue()

    def on_mount(self) -> None:
        self._debounce = None
        self._continue_ids: set[int] = set()
        self._upcoming_source: list = []
        self._rail_was_showing = self._rail_showing()
        # Title widths per list, pooled into the one grid every section is cut
        # to. See `_cols_for`.
        self._title_widths: dict[str, list[int]] = {}
        self._grid: Columns | None = None
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
        # The rail is built from every row, including the ones about to be
        # hidden from the list — feeding it the filtered set would take the show
        # off the rail, which would then put the row back, and the two would
        # flip against each other on every repaint.
        self._upcoming_source = [a for a, _, _ in rows]
        shown = self._without_rail_duplicates(rows)
        cols = self._cols_for([r for _, r, _ in shown], "continue")
        for anime, row, resume in shown:
            lv.append(AnimeItem(anime, row, cols, resume_episode=resume))
        self._set_section("#sec-continue", "Continue Watching", len(shown))

        # A show you are already watching does not need to be advertised again
        # further down the page: Seasonal listed four of these twice, with
        # different metadata each time, which read as two different shows.
        # Hidden rows count too — one you are caught up on is still one you are
        # watching, and should not reappear in Seasonal just because the rail is
        # carrying its countdown now.
        self._continue_ids = {a.id.anilist for a, _, _ in rows}
        # Re-size first: the rail's width depends on how much room the rows
        # turned out to need, which is only known now they exist.
        self._size_rail()
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
        cols = self._cols_for([r for _, r in built], "favorites")
        for anime, row in built:
            lv.append(AnimeItem(anime, row, cols))
        self._set_section("#sec-favorites", "Favorites", len(items))
        self._size_rail()

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
        cols = self._cols_for([r for _, r in built], "seasonal")
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
        cols = self._cols_for([r for _, r in built], "trending")
        for a, row in built:
            lv.append(AnimeItem(a, row, cols))
        self._set_section("#sec-trending", "Trending", len(animes))
        self._size_rail()

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
        cols = self._cols_for([row for _, row in built], "results")
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
        # It is also the one place that knows the sample the shared grid is cut
        # from has just grown, so the sections that arrived earlier can follow
        # it. A no-op once nothing has changed.
        #
        # After the refresh, not now: the grid is cut to a *measured* row, and
        # the rows this section just appended have no size until Textual has laid
        # them out. Called directly, `_row_space` finds every candidate still
        # reporting zero and falls back to the estimate — which is the thing the
        # measurement exists to replace.
        self.call_after_refresh(self._apply_grid)
        self._size_rail()
