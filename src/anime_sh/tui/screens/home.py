"""Home screen: search-as-you-type, continue watching, this season, trending."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import date, datetime, timezone

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Input,
    Label,
    ListView,
    Static,
)

from ...domain.models import Season, Status
from ..format import RANK_WAITING, browse_cells, continue_cells
from ..rows import (
    CHROME,
    Columns,
    columns_for_space,
    title_cells,
    title_target_from,
)
from ..coverart import (
    fetch_cover,
    graphics_cover_widget,
    graphics_protocol_active,
    render_cover,
)
from ..preview import render as preview_render
from ..shell import (
    SECTIONS,
    ActionBar,
    NavRail,
    Section,
    TopBar,
    band,
    empty_state,
    nav_width,
    spaced,
)
from ..upcoming import render, schedule, scheduled_ids
from ..widgets import AnimeItem
from .sources import SourcesScreen


#: Search results are a shelf like any other for layout purposes, but they are
#: not a *destination* — there is no nav row for them, because you get there by
#: typing rather than by choosing.
_RESULTS_SECTION = Section("", "", "Results", "#results", "#sec-results")


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
        # Tab is documented — in the README and on the `?` sheet — as "next
        # section", and Textual's default focus chain does not do that. Left to
        # itself it walks every focusable widget, which here means `#body` and
        # `#rail`: scroll containers that take focus, show no cursor, and turn
        # the arrow keys into panel scrolling. Four presses of Tab left you
        # somewhere with nothing selected and no way to tell why.
        Binding("tab", "next_section", "Next section", show=False),
        Binding("shift+tab", "previous_section", "Previous section", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "cursor_top", "Top", show=False),
        Binding("G", "cursor_bottom", "Bottom", show=False),
        # `v` for view, matching the detail screen's `v`. Same idea on both —
        # "change how this screen is laid out" — so it is one key to learn
        # rather than two, and it means something on whichever screen you are.
        Binding("v", "cycle_density", "Density"),
        # Progressive disclosure, in one key. A shelf shows a sample and says
        # how much more there is; `z` is how you ask for the rest. Deliberately
        # not a separate navigation concept — it expands whatever Tab has
        # already landed on, so there is nothing new to learn about where you
        # are, only about how much of it you can see.
        Binding("z", "zoom", "Expand", show=False),
        Binding("1", "jump('#continue')", "Continue", show=False),
        Binding("2", "jump('#favorites')", "Favourites", show=False),
        Binding("3", "jump('#seasonal')", "This season", show=False),
        Binding("4", "jump('#trending')", "Trending", show=False),
    ]

    @staticmethod
    def _configured_density() -> str:
        """The home density from config, or the default if it cannot be read."""
        from ...layout_names import DEFAULT_DENSITY, DENSITIES

        try:
            from ...config import load_config

            value = load_config().ui.density
        except Exception:
            return DEFAULT_DENSITY
        return value if value in DENSITIES else DEFAULT_DENSITY

    def _apply_density(self) -> None:
        self.set_class(self._density == "compact", "-dense")

    def action_cycle_density(self) -> None:
        """Swap the home screen between comfortable and compact, and remember
        which. Re-cuts the grid afterwards: the plate's padding is part of the
        room a row has, so the columns are measured against a width that has
        just changed."""
        from ...layout_names import DENSITIES

        self._density = DENSITIES[
            (DENSITIES.index(self._density) + 1) % len(DENSITIES)
        ]
        self._apply_density()
        try:
            from ...config import set_config_value

            set_config_value("ui.density", self._density)
        except Exception as e:
            self.notify(f"Couldn't save the density: {e}", severity="warning")
        self.notify(f"Density: {self._density}")
        self.call_after_refresh(self._apply_grid)

    def action_clear_search(self) -> None:
        box = self.query_one("#search", Input)
        if box.value:
            box.value = ""  # Input.Changed puts the browse sections back
            return
        # Already empty: Esc closes the box and gives the rows their rows back.
        if box.display:
            box.display = False
            self._paint_shell()
            for wid in self._FOCUS_ORDER:
                try:
                    lv = self.query_one(wid, ListView)
                except Exception:
                    continue
                if lv.display and len(lv.children):
                    lv.focus()
                    break

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
    def _sections(self) -> list[ListView]:
        """The lists Tab moves between: on screen, and with something in them.

        An empty or hidden list is not a place to land — during a search the
        browse sections are hidden, and Favorites stays empty until you star
        something.
        """
        out = []
        for wid in (*self._FOCUS_ORDER, "#results"):
            try:
                lv = self.query_one(wid, ListView)
            except Exception:
                continue
            if lv.display and len(lv.children):
                out.append(lv)
        return out

    def _cycle_section(self, step: int) -> None:
        sections = self._sections()
        if not sections:
            return
        current = self._focused_list()
        index = sections.index(current) if current in sections else -step
        sections[(index + step) % len(sections)].focus()

    # -- shelves, zoom and the shell ---------------------------------------- #
    #: What one shelf costs besides its rows: a label and the blank line that
    #: separates it from the next shelf.
    _SHELF_CHROME = 2
    #: Fewest rows worth showing. Below this a shelf is a label with a sample so
    #: small it says nothing, and the screen is better off scrolling.
    _SHELF_MIN = 3
    #: Most rows one shelf takes on a short terminal — the real ceiling grows
    #: with the window (see `_compute_caps`), because a shelf that stops at ten
    #: rows on a 60-row screen leaves half the column empty. What this floor is
    #: for is the other direction: no one shelf swallowing a small window and
    #: pushing the rest below the fold, which is the original complaint.
    _SHELF_MAX = 10

    def _shelf_cap(self) -> int:
        """Rows per shelf, divided out of the height actually available.

        A fixed table was tried first and under-filled by ten rows at 120×40 —
        four shelves of six on a screen with room for seven, which left a block
        of dead space under Trending while the label above it still said there
        were four more. The height is known; dividing it is strictly better than
        guessing at it.

        Four shelves at twenty rows each is what this exists to stop: the old
        screen put eighteen Continue rows on a forty-row terminal and pushed
        Trending below the fold, where nobody ever saw it.
        """
        caps = self._compute_caps()
        return max(caps.values()) if caps else self._SHELF_MIN

    def _compute_caps(self) -> dict:
        """Rows for each shelf, by list id.

        Not one number shared by all of them. Favourites holds two rows and was
        being handed the same seventh of the screen as Trending's ten, so a
        fifth of the window sat empty under a shelf that had nothing more to
        put there while the shelf below it still said "7 of 19".

        Shortest first, each taking only what it needs, and whatever it does not
        use going back into the pot for the rest.
        """
        shelves = self._visible_shelves()
        if getattr(self, "_zoomed", False) or not shelves:
            return {s.list_id: 1000 for s in shelves}

        # The two bars, and the search box when it is open.
        taken = 2
        try:
            if self.query_one("#search", Input).display:
                taken += 2
        except Exception:
            pass
        room = (self.size.height or 40) - taken - len(shelves) * self._SHELF_CHROME

        sizes = {}
        for s in shelves:
            try:
                sizes[s.list_id] = len(self.query_one(s.list_id, ListView).children)
            except Exception:
                sizes[s.list_id] = 0

        # Seed every shelf with the fewest rows worth showing, then hand the
        # rest out in weighted rounds until nothing can take any more.
        #
        # One proportional pass was tried and under-filled badly: a shelf
        # clamped to its own length hands nothing back, so at 200×60 the four
        # shelves took 36 of 59 rows and twenty-three sat empty beneath a label
        # that still said "8 of 19". The note here used to call that leftover a
        # margin; the screenshot calls it an empty half-screen.
        #
        # Handing it back *by weight* is what keeps the hierarchy that single
        # pass was protecting. An earlier attempt redistributed it evenly and
        # flattened exactly that: This Season drew level with Continue Watching
        # and the screen read as four equal blocks again. Here Continue takes
        # three fifths of every round, so it stays visibly the largest thing.
        ceiling = max(self._SHELF_MAX, room // 2)
        want = {s.list_id: min(sizes[s.list_id] or self._SHELF_MIN, ceiling)
                for s in shelves}
        caps = {s.list_id: min(want[s.list_id], self._SHELF_MIN) for s in shelves}
        pool = room - sum(caps.values())
        while pool > 0:
            hungry = [s for s in shelves if caps[s.list_id] < want[s.list_id]]
            if not hungry:
                break
            weight = sum(s.weight for s in hungry)
            moved = 0
            for s in hungry:
                if moved >= pool:
                    break
                give = min(want[s.list_id] - caps[s.list_id], pool - moved,
                           max(1, round(pool * s.weight / weight)))
                caps[s.list_id] += give
                moved += give
            if not moved:
                break
            pool -= moved
        return caps

    def _visible_shelves(self) -> list:
        """The shelves currently on screen — what the height has to divide by.

        Favourites hides itself when empty and the browse shelves hide during a
        search, so the number is not a constant; dividing by four when two are
        showing halves the rows for no reason.
        """
        out = []
        for section in (*SECTIONS, _RESULTS_SECTION):
            try:
                lv = self.query_one(section.list_id, ListView)
            except Exception:
                continue
            if lv.display and len(lv.children):
                out.append(section)
        return out

    def _apply_caps(self) -> None:
        """Cut every shelf to its share of the screen.

        Done with `max-height` rather than by building fewer rows, so expanding
        a shelf costs a style change instead of a rebuild — and so the rows that
        are not currently visible are still *there* to scroll through, which is
        what makes a capped shelf a sample rather than a truncation.
        """
        caps = self._compute_caps()
        for section in (*SECTIONS, _RESULTS_SECTION):
            try:
                lv = self.query_one(section.list_id, ListView)
            except Exception:
                continue
            zoomed = getattr(self, "_zoomed", False)
            target = getattr(self, "_zoom_target", None)
            if zoomed and target is not None:
                lv.display = section.list_id == target
                try:
                    self.query_one(section.head_id).display = lv.display
                except Exception:
                    pass
                if lv.display:
                    lv.styles.max_height = None
                continue
            lv.styles.max_height = caps.get(section.list_id, self._SHELF_MIN)

    def action_zoom(self) -> None:
        """Expand the focused shelf to the whole screen, or collapse back.

        The nav rail names the shelves and `z` is how you open one; together
        they are the navigation model. Nothing else changes about where you are,
        which is why this is one key rather than a mode.
        """
        focused = self._focused_list()
        if not getattr(self, "_zoomed", False):
            if focused is None:
                self.notify("Nothing to expand — pick a shelf first.")
                return
            self._zoomed = True
            self._zoom_target = f"#{focused.id}"
        else:
            self._zoomed = False
            self._zoom_target = None
            self._show_home_sections(not self._searching())
        self._apply_caps()
        self._paint_shell()
        self.call_after_refresh(self._apply_grid)
        if focused is not None:
            focused.focus()

    def action_jump(self, list_id: str) -> None:
        """Put the keyboard on a named shelf, expanding it if it is hidden."""
        try:
            lv = self.query_one(list_id, ListView)
        except Exception:
            return
        if getattr(self, "_zoomed", False):
            self._zoom_target = list_id
            self._apply_caps()
        if not (lv.display and len(lv.children)):
            self.notify("Nothing there yet.")
            return
        lv.focus()
        if lv.index is None:
            lv.index = 0
        self._paint_shell()

    def _searching(self) -> bool:
        try:
            return bool(self.query_one("#search", Input).value.strip())
        except Exception:
            return False

    def _paint_shell(self) -> None:
        """Repaint the furniture: where you are, what else there is, what the
        thing under the cursor can do."""
        width = self.size.width or 100
        focused = self._focused_list()
        current = f"#{focused.id}" if focused is not None else None
        counts = {}
        for section in SECTIONS:
            try:
                counts[section.list_id] = len(
                    self.query_one(section.list_id, ListView).children
                )
            except Exception:
                counts[section.list_id] = 0

        here = next((s.label for s in SECTIONS if s.list_id == current), None)
        if self._searching():
            here = "Search"
        try:
            self.query_one("#topbar", TopBar).render_bar(here or "Home", width)
        except Exception:
            pass
        searching = self._searching()
        try:
            nav = self.query_one("#nav", NavRail)
            # Emptied while searching, but its column is kept.
            #
            # Emptied because searching hides all four shelves, so the rail was
            # a map to four destinations that did not exist, with four digits
            # that jumped to hidden lists behind it.
            #
            # Kept because panels that move cost more than panels that go quiet.
            # Hiding it outright was the first version and it shifted the whole
            # content column five cells left — twenty at 160 — on the first
            # character typed, so the result list landed somewhere the shelves
            # had never been. Spatial memory is the navigation in an interface
            # this dense; an empty gutter reads as margin, while a list that
            # jumps sideways as you type reads as a different screen.
            nav.display = nav_width(width) > 0
            if nav.display:
                nav.render_nav(None if searching else current,
                               nav_width(width), counts,
                               blank=searching)
        except Exception:
            pass
        try:
            bar = self.query_one("#actionbar", ActionBar)
            contextual = []
            if focused is not None and focused.index is not None:
                item = (focused.children[focused.index]
                        if focused.index < len(focused.children) else None)
                if isinstance(item, AnimeItem):
                    contextual = [("↵", "open")]
                    if item.resume_episode is not None:
                        verb = "resume" if item.fraction > 0 else "play"
                        contextual = [("↵", verb)]
            # "tab next shelf" with one shelf on screen names a key that does
            # nothing. What you actually want from a result list is out of it.
            contextual.append(("esc", "back") if searching else ("tab", "next shelf"))
            bar.render_actions(width, contextual=contextual,
                               zoomed=getattr(self, "_zoomed", False))
        except Exception:
            pass

    def on_descendant_focus(self, event) -> None:
        # The rail marks whichever shelf the keyboard is on, so it has to follow
        # Tab rather than be driven separately.
        self._paint_shell()

    def action_next_section(self) -> None:
        self._cycle_section(1)

    def action_previous_section(self) -> None:
        self._cycle_section(-1)

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
        # The shell, not a Header and a Footer. Textual's Header spends a row on
        # a centred app title and a clock — the clock is the terminal's job, and
        # the one thing the old bar never said was which section you were in.
        yield TopBar(id="topbar")
        # Hidden until `/` asks for it. An always-on box spent four rows — a
        # margin, a tall border and the field — to show a placeholder telling
        # you which key opens it, on a screen whose scarcest resource is rows.
        # The top bar says `/ search`, which is the same information for free.
        yield Input(placeholder="Search anime…  (esc to close)", id="search")
        # Region B (the rows) and Region C (the context rail) side by side. The
        # rows cap themselves at a readable measure, so on a wide terminal they
        # stop around column 96 and leave most of the window empty; the rail is
        # what that space is for. It is hidden below 120 columns — see
        # `_size_rail` — so a small terminal is exactly as it was.
        with Horizontal(id="columns"):
            # The nav rail is a map, not a control: Tab already moves between
            # shelves, so a rail that also took focus would be a second way to
            # do one thing and a second place for the cursor to get lost in.
            yield NavRail(id="nav")
            with VerticalScroll(id="body"):
                yield Label(spaced("Continue watching"), classes="shelf-label",
                            id="sec-continue")
                yield ListView(id="continue")
                yield Label(spaced("Favourites"), classes="shelf-label",
                            id="sec-favorites")
                yield ListView(id="favorites")
                yield Label(spaced("This season"), classes="shelf-label",
                            id="sec-seasonal")
                yield ListView(id="seasonal")
                yield Label(spaced("Trending"), classes="shelf-label", id="sec-trending")
                yield ListView(id="trending")
                yield Label(spaced("Results"), classes="shelf-label", id="sec-results")
                yield ListView(id="results")
                # Searching hides the browse sections, so a query that matches
                # nothing left the whole screen blank under a "Results" heading with
                # no indication of what had happened. This is what fills that space.
                yield Label("", id="results-empty")
            with VerticalScroll(id="rail"):
                # The hero. Poster first, then the show, then the one thing to
                # press. It used to be a second list of episodes and nothing
                # else, which left a client for a visual medium with no image on
                # its main screen and no focal point anywhere.
                yield Container(id="rail-cover")
                yield Static("", id="rail-preview")
                yield Label(spaced("Coming up"), classes="shelf-label",
                            id="sec-rail")
                yield Static("", id="rail-body")
        yield ActionBar(id="actionbar")

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
        taken = nav_width(width)
        if width >= self._RAIL_MIN_WIDTH:
            taken += self._rail_base(width)
        return width - taken

    # Region C appears only when there is genuinely room for it. Below this the
    # rows alone fill the window and a rail would be stealing from them; the
    # monospace-design standard puts the same boundary at 120 columns.
    # The poster's width in cells. Region C is narrower than the detail
    # screen's column, and a cover wider than the text beside it stops being an
    # illustration and becomes the panel.
    _COVER_COLS = 22

    # How long the cursor must rest on a row before its poster is requested.
    # Long enough that walking a list never fetches anything; short enough that
    # stopping on a row feels like it responds. A named constant so a test can
    # widen or close the window instead of racing a real clock — the first
    # version of that test pressed keys and hoped, and failed one run in three.
    _COVER_DEBOUNCE_S = 0.35

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

    def _size_nav(self) -> None:
        """Show, hide and size the nav rail for the current terminal.

        Gone entirely below 80 cells, icons only to 120, labels past that. At
        80–119 every column spent on a word is one the titles do not get, and
        the icons carry the order on their own once the labels have been seen.
        """
        try:
            nav = self.query_one("#nav", NavRail)
        except Exception:
            return
        width = nav_width(self.size.width or 100)
        nav.display = width > 0
        if width:
            nav.styles.width = width

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
            entry
            for entry in rows
            if entry[1].rank != RANK_WAITING or entry[0].id.anilist not in on_rail
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
        # Not while searching. This runs whenever a list reloads, so without the
        # guard any background refresh landing mid-search put the schedule back
        # under the result the hero was describing.
        showing = not self._searching()
        self.query_one("#sec-rail").display = showing
        body.display = showing

    def on_resize(self) -> None:
        was_showing = getattr(self, "_rail_was_showing", None)
        self._size_rail()
        self._size_nav()
        self._apply_caps()
        self._paint_shell()
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
        self._density = self._configured_density()
        self._apply_density()
        self._debounce = None
        self._continue_ids: set[int] = set()
        self._upcoming_source: list = []
        self._rail_was_showing = self._rail_showing()
        # Title widths per list, pooled into the one grid every section is cut
        # to. See `_cols_for`.
        self._title_widths: dict[str, list[int]] = {}
        self._grid: Columns | None = None
        # Posters, by AniList id. Small, and the alternative is
        # re-fetching an image every time the cursor passes a row.
        self._covers: dict[int, bytes] = {}
        self._cover_timer = None
        self._preview_id: int | None = None
        self._show_home_sections(True)
        self.query_one("#sec-results").display = False
        self.query_one("#results").display = False
        self.query_one("#results-empty").display = False
        self._zoomed = False
        self._zoom_target: str | None = None
        self.query_one("#search", Input).display = False
        self._size_rail()
        self._apply_caps()
        self._paint_shell()
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
            # Only a part-watched episode has a meaningful fraction; a row that
            # is merely "ready" is at zero and must not draw a progress bar in
            # the rail as though it were started. `fraction` is already 0.0 when
            # the duration is unknown, so the duration does not need testing here
            # too -- doing that is what made a positioned-but-duration-less row
            # look unstarted everywhere at once.
            rows.append((anime, row, resume,
                         it.progress.fraction if it.progress.resumable else 0.0))

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
        self._upcoming_source = [a for a, _, _, _ in rows]
        shown = self._without_rail_duplicates(rows)
        cols = self._cols_for([r for _, r, _, _ in shown], "continue")
        for anime, row, resume, fraction in shown:
            lv.append(AnimeItem(anime, row, cols, resume_episode=resume,
                                fraction=fraction))
        self._set_section("#sec-continue", "Continue watching", len(shown))

        # A show you are already watching does not need to be advertised again
        # further down the page: Seasonal listed four of these twice, with
        # different metadata each time, which read as two different shows.
        # Hidden rows count too — one you are caught up on is still one you are
        # watching, and should not reappear in Seasonal just because the rail is
        # carrying its countdown now.
        self._continue_ids = {a.id.anilist for a, _, _, _ in rows}
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
        self._set_section("#sec-seasonal", "This season", shown)

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
        self._set_section("#sec-favorites", "Favourites", len(items))
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
            self._mark_section_unavailable("#sec-seasonal", "This season",
                                           "#seasonal")
            return
        finally:
            lv.loading = False
        self._clear_section_unavailable("#sec-seasonal", "#seasonal")
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
            self._mark_section_unavailable("#sec-trending", "Trending", "#trending")
            return
        finally:
            lv.loading = False
        self._clear_section_unavailable("#sec-trending", "#trending")
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
        # Before the debounce, not after it: the bar said "Home" for the third
        # of a second you were typing into the search box, which is the whole
        # time the screen had nothing else to say.
        self._paint_shell()
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
        # Broken into short lines by hand rather than left to wrap. The hint was
        # one long sentence and came out clipped mid-word — "partial titles like
        # fri w" — which is worse than no hint at all, because the reader cannot
        # tell whether the advice ended there or the screen gave up.
        label.update(
            # Dim rather than italic: italic is not one of the four text
            # treatments this UI uses, and a fair number of terminals render it
            # as reverse video or drop it entirely.
            f"  [b]No matches for[/][dim] {shown}[/dim]\n"
            f"\n"
            f"  [dim]Try fewer words, or a different spelling.[/dim]\n"
            f"  [dim]Partial titles work — [/][cyan]fri[/][dim] finds Frieren.[/dim]\n"
            f"  [dim]Press [/][cyan]esc[/][dim] to clear the search.[/dim]"
        )
        label.display = True
        # A heading over nothing. "Results" above an empty plate above a notice
        # saying there are none is the same fact three times, and the notice is
        # the only one of the three that says anything useful.
        with contextlib.suppress(Exception):
            self.query_one("#sec-results").display = False
            # And the plate under it. An empty ListView still draws its own
            # vertical padding, so hiding only the heading left two blank rows
            # between the search box and the notice explaining them.
            self.query_one("#results").display = False
        # The hero was still describing whichever row the cursor sat on before
        # the search — a show that is, by definition, not among the results. A
        # panel confidently detailing something the list does not contain is
        # worse than an empty panel.
        with contextlib.suppress(Exception):
            self.query_one("#rail-preview", Static).update(
                empty_state("Nothing to show",
                            "Pick a result to see it here.",
                            self._rail_width(self.size.width or 100) - 4)
            )
            self._preview_id = None

    # -- navigation --------------------------------------------------------- #
    # -- Region C: the row the cursor is on --------------------------------- #
    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Repaint the preview for the newly highlighted row."""
        item = event.item
        if isinstance(item, AnimeItem):
            self._show_preview(item)

    def _show_preview(self, item: AnimeItem) -> None:
        """Draw Region C's header block for one row, and ask for its poster.

        The text goes up immediately — every field it needs is already on the
        Anime the row was built from. Only the image has to be fetched, and it
        arrives separately so the panel is never waiting on the network to say
        what it already knows.
        """
        if not self._rail_showing():
            return
        try:
            panel = self.query_one("#rail-preview", Static)
        except Exception:
            return
        self._preview_id = item.anime.id.anilist
        width = self._rail_width(self.size.width or 100) - 4
        panel.update(preview_render(
            item.anime, width,
            resume_episode=item.resume_episode,
            fraction=item.fraction,
            synopsis_lines=self._synopsis_lines(),
        ))
        self._request_cover(item.anime)

    #: Rows the hero needs for everything that is not the synopsis: the title
    #: over two lines, the facts, the tags, the status, the progress bar, the
    #: action, and the blank line before each of them.
    _HERO_CHROME = 14

    def _synopsis_lines(self) -> int:
        """How much of the description to show, by how much room there is.

        Four lines regardless was fine beside a full home screen and absurd
        beside a two-result search: the schedule hides while searching, so the
        hero had thirty blank rows under a paragraph cut off mid-sentence. The
        space a short result list frees belongs to the one result you are
        looking at — that is the whole argument for a hero.

        Floored at four so this can only ever add, never take away what the
        panel showed before.
        """
        free = (self.size.height or 40) - self._HERO_CHROME
        if not self._searching():
            # The schedule is below the hero and wants the rest of the column.
            return 4
        return max(4, min(free, 24))

    def _request_cover(self, anime) -> None:
        """Show this show's poster, fetching it at most once.

        Debounced: holding an arrow key walks the cursor through a dozen rows a
        second, and a request per row would be a launch storm with a different
        trigger — the same mistake that once earned this screen a 429 on every
        start. The timer is reset on each move, so only the row you actually
        stopped on is fetched.
        """
        self._covers = getattr(self, "_covers", {})
        anilist = anime.id.anilist
        if anilist in self._covers:
            self._paint_cover(anilist)
            return
        self._clear_cover()
        if not anime.cover_url:
            return
        if (timer := getattr(self, "_cover_timer", None)) is not None:
            timer.stop()
        self._cover_timer = self.set_timer(
            self._COVER_DEBOUNCE_S,
            lambda: self._fetch_cover(anilist, anime.cover_url),
        )

    @work(exclusive=True, group="cover")
    async def _fetch_cover(self, anilist: int, url: str) -> None:
        data = await fetch_cover(url)
        # The attempt is recorded even when it fails, so a cover that 404s or
        # times out is tried once rather than re-requested every time the cursor
        # passes its row. An empty entry paints nothing, which is what a missing
        # poster should do anyway.
        self._covers[anilist] = data or b""
        # The cursor may have moved on while this was in flight; painting it
        # then would put one show's poster beside another show's text.
        if data and getattr(self, "_preview_id", None) == anilist:
            self._paint_cover(anilist)

    def _clear_cover(self) -> None:
        try:
            self.query_one("#rail-cover", Container).remove_children()
        except Exception:
            pass

    def _paint_cover(self, anilist: int) -> None:
        """Mount the cached poster. Decoration only — any failure leaves the
        panel textual rather than costing the screen."""
        data = self._covers.get(anilist)
        if not data:
            return
        try:
            container = self.query_one("#rail-cover", Container)
        except Exception:
            return
        container.remove_children()
        cols = min(self._COVER_COLS,
                   max(8, self._rail_width(self.size.width or 100) - 4))
        if graphics_protocol_active():
            widget = graphics_cover_widget(data, cols)
            if widget is not None:
                try:
                    container.mount(widget)
                    return
                except Exception:
                    pass
        art = render_cover(data, cols=cols)
        if art is not None:
            try:
                container.mount(Static(art))
            except Exception:
                pass

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
        # Next week's broadcast schedule is a home-screen answer to "what is on
        # tonight". While you are searching for something else it is sixteen
        # rows of the hero spent on a question nobody asked — so the hero keeps
        # the highlighted result and nothing else.
        for wid in ("#sec-rail", "#rail-body"):
            with contextlib.suppress(Exception):
                self.query_one(wid).display = not on
        self._apply_caps()
        self._paint_shell()

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

    def _clear_section_unavailable(self, sec_id: str, list_id: str) -> None:
        """A section that has just loaded is no longer unavailable."""
        getattr(self, "_unavailable", set()).discard(sec_id)
        with contextlib.suppress(Exception):
            self.query_one(list_id).display = True

    def _mark_section_unavailable(self, sec_id: str, base: str, list_id: str) -> None:
        """Say a section could not be loaded, instead of leaving it blank.

        The failure was already announced — once, in a toast, which is gone by
        the time anyone looks. What was left behind was a heading with no count
        above an empty plate, and an empty "Trending" does not read as "we could
        not reach AniList", it reads as "nothing is trending".

        Learned the day AniList disabled its own public API: two sections went
        silently empty and the app looked broken rather than blocked.
        """
        try:
            label = self.query_one(sec_id, Label)
        except Exception:
            return
        # Recorded, because another worker gets there afterwards. Continue
        # Watching finishes last and calls `_hide_seasonal_duplicates`, which
        # re-runs `_set_section` — which quietly overwrote this heading with an
        # ordinary empty one, so seasonal went back to looking merely empty.
        if not hasattr(self, "_unavailable"):
            self._unavailable: set[str] = set()
        self._unavailable.add(sec_id)
        room = max(12, self._row_space() - 2)
        tail = "unavailable"
        rule = max(1, room - len(base) - len(tail) - 4)
        label.update(
            f"[b]{base}[/b]  [dim]{'─' * rule}[/dim]  [$warning]{tail}[/$warning]"
        )
        # Hide the plate rather than leave an empty one: a blank surface reads
        # as a list that happens to have no rows.
        with contextlib.suppress(Exception):
            self.query_one(list_id).display = False

    def _set_section(self, sec_id: str, base: str, count: int) -> None:
        """Draw a shelf label: letterspaced caps, the count, and how much more
        there is.

        This used to draw `Continue Watching ───────────────── 18` — forty cells
        of line art carrying one integer, three times down the screen. The rule
        was doing a job the eye does by itself once the label is set differently
        from everything else on screen, and letterspaced caps are the one
        treatment nothing else here uses.

        The count is not decoration: when a shelf is capped it is the only thing
        saying that the eight rows you can see are not all of them.
        """
        if sec_id in getattr(self, "_unavailable", ()):
            return  # the section could not load; do not relabel it as empty
        try:
            label = self.query_one(sec_id, Label)
        except Exception:
            return
        list_id = next((x.list_id for x in (*SECTIONS, _RESULTS_SECTION)
                        if x.head_id == sec_id), None)
        shown = self._compute_caps().get(list_id, self._SHELF_MIN)
        more = count - shown if count > shown and not self._zoomed else 0
        tail = f"[dim]{count}[/dim]" if count else ""
        if more:
            # The affordance, not just a number: a shelf that silently stops at
            # eight rows looks like a library with eight shows in it.
            tail = f"[dim]{shown} of {count}[/dim]  [$accent]z[/]"
        label.update(f"[b]{spaced(base)}[/b]   {tail}".rstrip())
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
