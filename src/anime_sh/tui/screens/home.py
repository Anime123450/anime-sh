"""Home screen: a cinematic hero over horizontal shelves of cover art.

The composition, and why it is this one. The first redesign of this screen
produced three tall stacked lists with an information panel down the right-hand
side — tidier than what came before, and structurally the same thing, which was
the one arrangement the brief asked it to move away from. Rows are an efficient
way to present a database. Anime is not a database; the poster *is* the
metadata, and a person recognises a show from a thumbnail faster than from its
name set in a column.

So: one show is large, at the top, with everything known about it (`preview`
draws that block). Everything else is a poster on a shelf you walk sideways,
and the hero follows the cursor — so the detail is on screen for exactly one
show, the one you are pointing at, and never for all of them at once.

What that costs, stated plainly because it is a real cost: a shelf of posters is
thirteen rows where a list of rows was one. At 190×50 this is the hero and two
and a half shelves; the old screen fitted four lists in the same space. Fewer
things, bigger, is the trade.

Everything below the composition is data loading, and is the same as it was:
five independent workers (continue, favourites, seasonal, trending, search),
each degrading to an empty shelf and a notice rather than taking the app down.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import date, datetime, timezone

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Label, Static

from ...domain.models import Season, Status
from ..cards import PosterCard, Shelf
from ..coverart import (
    cached_cover,
    cover_rows,
    fetch_cover,
    graphics_cover_widget,
    graphics_protocol_active,
    render_cover,
)
from ..format import card_caption
from ..preview import render as preview_render
from ..shell import (
    SECTIONS,
    ActionBar,
    Section,
    TopBar,
    band,
    empty_state,
    spaced,
)
from .sources import SourcesScreen

#: Search results are a shelf like any other for layout purposes, but they are
#: not a *destination* — there is no numbered key for them, because you get
#: there by typing rather than by choosing.
_RESULTS_SECTION = Section("Results", "#results", "#sec-results")


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
    reading it as "finished, nothing can change" would pin the card to a
    schedule it never had and offer an unreleased episode as though it were
    waiting for you, which is the bug the cached schedule exists to prevent.
    """
    if anime.is_airing:
        # A cached next episode still in the future is everything the card
        # needs: the countdown ticks locally, so there is nothing to fetch.
        return anime.next_airing_at is None or anime.next_airing_at <= now
    if anime.status in (Status.FINISHED, Status.CANCELLED):
        return False  # the schedule is final and will not change again
    return True  # UNKNOWN, NOT_YET_RELEASED, HIATUS — we genuinely do not know


class HomeScreen(Screen):
    BINDINGS = [
        # Escape is bound app-wide to "go back", which on the base screen has
        # nothing to pop and so did nothing at all — leaving no way out of a
        # search except selecting the box and deleting it by hand.
        Binding("escape", "clear_search", "Clear search", show=False),
        # Tab is documented — in the README and on the `?` sheet — as "next
        # shelf", and Textual's default focus chain does not do that. Left to
        # itself it walks every focusable widget, which here includes `#body`:
        # a scroll container that takes focus, shows no cursor, and turns the
        # arrow keys into panel scrolling.
        Binding("tab", "next_section", "Next shelf", show=False),
        Binding("shift+tab", "previous_section", "Previous shelf", show=False),
        # `v` for view, matching the detail screen's `v`. Same idea on both —
        # "change how this screen is laid out" — so it is one key to learn
        # rather than two, and it means something on whichever screen you are.
        Binding("v", "cycle_density", "Density"),
        Binding("g", "first_section", "First shelf", show=False),
        Binding("G", "last_section", "Last shelf", show=False),
        Binding("1", "jump('#continue')", "Continue", show=False),
        Binding("2", "jump('#favorites')", "Favourites", show=False),
        Binding("3", "jump('#seasonal')", "This season", show=False),
        Binding("4", "jump('#trending')", "Trending", show=False),
    ]

    #: A poster's width in the hero, by band. The hero's art is the one image on
    #: the screen meant to be *looked* at rather than recognised, so it gets as
    #: many cells as the band can spare — and `render_cover` turns that width
    #: into a height, which is what sets `_HERO_ROWS` below.
    _HERO_COLS = {"compact": 0, "standard": 16, "expanded": 20, "wide": 24}

    #: Rows the hero's text block spends on everything that is not the synopsis:
    #: the title over two lines, the facts, the tags, the status, the progress
    #: bar, the action, and the blank line before each of them.
    _HERO_CHROME = 10

    #: Below this the hero is not shown at all. At 24 rows it would leave six
    #: for the shelves, which is less than one card — so the screen would be a
    #: hero and nothing else, which is a detail screen with extra steps.
    _HERO_MIN_HEIGHT = 30

    #: The least the hero may be, whatever the poster measures. Its text block
    #: needs ten rows before a word of synopsis, and a hero showing none of the
    #: synopsis has spent the top of the screen saying less than the caption
    #: under the poster did.
    _HERO_MIN_ROWS = 12

    #: How many covers to have in flight at once. AniList's CDN is not the
    #: rate-limited part, but a shelf is a dozen images and four shelves is
    #: fifty: ungated, the first paint competes with itself for the connection
    #: pool and the posters arrive in no useful order.
    _COVER_GATE = 6

    # ------------------------------------------------------------------ #
    # composition
    # ------------------------------------------------------------------ #
    def compose(self) -> ComposeResult:
        # The shell, not a Header and a Footer. Textual's Header spends a row on
        # a centred app title and a clock — the clock is the terminal's job, and
        # the one thing the old bar never said was which section you were in.
        yield TopBar(id="topbar")
        # Hidden until `/` asks for it. An always-on box spent rows to show a
        # placeholder naming the key that opens it; the top bar says `/ search`,
        # which is the same information for free.
        yield Input(placeholder="Search anime…  (esc to close)", id="search")
        # The hero sits *outside* the scroll container on purpose. It is the
        # focal point and it describes what the cursor is on, so scrolling it
        # away would leave the shelves annotating something off-screen.
        with Horizontal(id="hero"):
            yield Container(id="hero-art")
            yield Static("", id="hero-text")
        with VerticalScroll(id="body"):
            for sec in (*SECTIONS, _RESULTS_SECTION):
                yield Label(spaced(sec.label), classes="shelf-label",
                            id=sec.head_id.lstrip("#"))
                yield Shelf(id=sec.list_id.lstrip("#"))
            # Searching hides the browse shelves, so a query that matches
            # nothing left the whole screen blank under a "Results" heading with
            # no indication of what had happened. This is what fills that space.
            yield Label("", id="results-empty")
        yield ActionBar(id="actionbar")

    def on_mount(self) -> None:
        self._density = self._configured_density()
        self._apply_density()
        self._debounce = None
        self._continue_ids: set[int] = set()
        #: Cover bytes by AniList id, so moving the cursor back along a shelf
        #: never re-reads the disk and never re-fetches. The disk cache in
        #: `coverart` is what makes the *first* paint cheap; this is what makes
        #: the hundredth free.
        self._covers: dict[int, bytes] = {}
        #: Watch progress by AniList id, for re-cutting a caption when a
        #: countdown ticks. The card carries the caption; the numbers behind it
        #: live here.
        self._progress: dict = {}
        self._hero_id: int | None = None
        self._unavailable: set[str] = set()
        self._focus_claimed: str | None = None
        self.query_one("#search", Input).display = False
        self._toggle_results(False)
        self._size_hero()
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

    # ------------------------------------------------------------------ #
    # layout
    # ------------------------------------------------------------------ #
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
        """Swap the home screen between comfortable and compact, and remember it.

        Compact buys two rows a shelf — the gap above the label and the gap
        under the cards — which is a whole extra shelf on a laptop terminal.
        It matters more now than it did over rows, not less: a poster is
        thirteen rows, so a 34-row window fits one shelf and a sliver, and the
        sliver is what this key turns into a second shelf.
        """
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

    def _hero_cols(self) -> int:
        return self._HERO_COLS[band(self.size.width or 100)]

    def _hero_rows(self) -> int:
        """Rows the hero occupies: whatever its poster comes back as.

        Derived rather than fixed. Pinned to the widest band's seventeen rows,
        a 120-column terminal reserved seventeen for a fourteen-row poster and
        left three blank between the hero and the first shelf — three rows is a
        quarter of a card, taken off the shelves to pad a gap.
        """
        return max(self._HERO_MIN_ROWS, cover_rows(self._hero_cols()))

    def _hero_showing(self) -> bool:
        """Whether there is room for the hero at all.

        Asked of the size rather than of the widget's `display`, because the
        first shelf can finish loading before `_size_hero` has run, and a
        `display` of False would then be read as "no hero" on a screen that is
        about to have one.
        """
        return ((self.size.height or 40) >= self._HERO_MIN_HEIGHT
                and self._hero_cols() > 0)

    def _size_hero(self) -> None:
        with contextlib.suppress(Exception):
            hero = self.query_one("#hero")
            hero.display = self._hero_showing()
            if not hero.display:
                return
            # +1 for the hero's own top padding, which lives in `app.tcss`.
            # Without it the poster is exactly one row taller than the box it is
            # in and loses its bottom row — the one that is mostly the foot of
            # the image, so it reads as a slightly wrong crop rather than as
            # clipping, which is why it survived a look.
            hero.styles.height = self._hero_rows() + 1
            self.query_one("#hero-art").styles.width = self._hero_cols()

    def _hero_text_width(self) -> int:
        """Cells the hero's prose gets: the screen, less the poster and padding.

        Measured off the widget where it can be. The hero's padding lives in
        `app.tcss` and the poster's width is set above, so computing it a second
        time here is how the two drift apart.
        """
        with contextlib.suppress(Exception):
            text = self.query_one("#hero-text", Static)
            if text.content_region.width > 0:
                return text.content_region.width
        return max(24, (self.size.width or 100) - self._hero_cols() - 8)

    def _synopsis_lines(self) -> int:
        """How much of the description the hero has room for.

        Floored at two: a one-line synopsis is a fragment, and a hero that shows
        a fragment of the plot has spent the top of the screen to say less than
        the caption under the poster did.
        """
        return max(2, self._hero_rows() - self._HERO_CHROME)

    def on_resize(self) -> None:
        self._size_hero()
        self._paint_shell()
        self._paint_hero()

    # ------------------------------------------------------------------ #
    # the hero
    # ------------------------------------------------------------------ #
    def _focused_shelf(self) -> Shelf | None:
        node = self.focused
        while node is not None:
            if isinstance(node, Shelf):
                return node
            node = node.parent
        return None

    def _current_card(self) -> PosterCard | None:
        shelf = self._focused_shelf()
        if shelf is None:
            return None
        cards = shelf.cards
        if not cards:
            return None
        return cards[min(shelf.index, len(cards) - 1)]

    def on_shelf_moved(self, event: Shelf.Moved) -> None:
        """The cursor landed on a card: the hero becomes that show."""
        self._paint_hero(event.card)
        self._paint_shell()

    def on_shelf_chosen(self, event: Shelf.Chosen) -> None:
        # Go through the source picker; it forwards straight to the detail
        # screen when there is only one match.
        card = event.card
        self.app.push_screen(
            SourcesScreen(card.anime, resume_episode=card.resume_episode)
        )

    def _paint_hero(self, card: PosterCard | None = None) -> None:
        """Draw the hero for ``card``, or repaint whatever it already has.

        The text goes up immediately — every field it needs is on the `Anime`
        the card was built from. Only the image has to be fetched, and it
        arrives separately, so the hero never waits on the network to say what
        it already knows.
        """
        if not self._hero_showing():
            return
        card = card or self._current_card()
        if card is None:
            return
        anime = card.anime
        self._hero_id = anime.id.anilist
        with contextlib.suppress(Exception):
            self.query_one("#hero-text", Static).update(preview_render(
                anime, self._hero_text_width(),
                resume_episode=card.resume_episode,
                fraction=card.fraction,
                synopsis_lines=self._synopsis_lines(),
            ))
        self._paint_hero_art(anime)

    def _paint_hero_art(self, anime) -> None:
        """Mount the hero's poster at hero size, or leave the space reserved.

        Decoration only: every failure here leaves a textual hero rather than
        costing the screen. Rendered from the same bytes the card is drawn from,
        at the larger width — a 14-cell poster scaled up to 24 is a 14-cell
        poster with bigger squares.
        """
        try:
            container = self.query_one("#hero-art", Container)
        except Exception:
            return
        container.remove_children()
        cols = self._hero_cols()
        data = self._cover_bytes(anime)
        if not data or cols <= 0:
            return
        if graphics_protocol_active():
            widget = graphics_cover_widget(data, cols)
            if widget is not None:
                with contextlib.suppress(Exception):
                    container.mount(widget)
                    return
        art = render_cover(data, cols=cols)
        if art is not None:
            with contextlib.suppress(Exception):
                container.mount(Static(art))

    # ------------------------------------------------------------------ #
    # covers
    # ------------------------------------------------------------------ #
    def _cover_bytes(self, anime) -> bytes | None:
        """This show's cover if it is already to hand, remembered in memory.

        A plain file read, so it is cheap enough to call while building a shelf
        — which is the point: on every launch after the first, a wall of twelve
        posters paints complete in the first frame instead of filling in one
        HTTPS round trip at a time.
        """
        anilist = anime.id.anilist
        if anilist in self._covers:
            return self._covers[anilist] or None
        if not anime.cover_url:
            return None
        if (data := cached_cover(anime.cover_url)) is not None:
            self._covers[anilist] = data
            return data
        return None

    def _fill_covers(self, cards: list[PosterCard]) -> None:
        """Give every card the cover it can have now, and fetch the rest.

        The split matters. Cards that can be filled from disk are filled
        *synchronously*, before the shelf is painted, so they do not flicker in
        one at a time on a second launch. Only genuine misses go to the network,
        and they land in space the card has already reserved — see
        `cards.placeholder`.
        """
        missing = []
        for card in cards:
            if (data := self._cover_bytes(card.anime)) is not None:
                card.set_cover(data)
            elif card.anime.cover_url:
                missing.append((card.anime.id.anilist, card.anime.cover_url))
        if missing:
            self._fetch_covers(missing)

    @work(group="covers")
    async def _fetch_covers(self, wanted: list[tuple[int, str]]) -> None:
        """Fetch the covers not yet on disk, a few at a time."""
        gate = asyncio.Semaphore(self._COVER_GATE)

        async def one(anilist: int, url: str) -> None:
            async with gate:
                if anilist in self._covers:
                    return  # another shelf wanted the same show and got there
                data = await fetch_cover(url)
                # The attempt is recorded even when it fails, so a cover that
                # 404s is tried once rather than re-requested by every shelf it
                # appears on. An empty entry paints nothing, which is what a
                # missing poster should do anyway.
                self._covers[anilist] = data or b""
                if data:
                    self._apply_cover(anilist, data)

        await asyncio.gather(*(one(a, u) for a, u in wanted))

    def _apply_cover(self, anilist: int, data: bytes) -> None:
        """Hand freshly-arrived bytes to every card showing that show.

        Every card, not the first: a show can be on two shelves at once —
        trending and this season overlap most weeks — and filling only one of
        them left the same poster blank beside itself.
        """
        for card in self.query(PosterCard):
            if card.anime.id.anilist == anilist:
                card.set_cover(data)
        if self._hero_id == anilist and (card := self._current_card()) is not None:
            self._paint_hero_art(card.anime)

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #
    def _shelves(self) -> list[Shelf]:
        """The shelves currently on screen, in the order Tab walks them.

        Only the ones *showing*: Continue Watching and Favourites hide
        themselves when empty, and a Tab that stopped on an invisible shelf left
        the screen with no cursor anywhere and no way to tell why.
        """
        out = []
        for sec in (*SECTIONS, _RESULTS_SECTION):
            with contextlib.suppress(Exception):
                shelf = self.query_one(sec.list_id, Shelf)
                if shelf.display and shelf.cards:
                    out.append(shelf)
        return out

    def _cycle_section(self, step: int) -> None:
        shelves = self._shelves()
        if not shelves:
            return
        current = self._focused_shelf()
        if current not in shelves:
            shelves[0].focus()
            return
        i = (shelves.index(current) + step) % len(shelves)
        shelves[i].focus()

    def action_next_section(self) -> None:
        self._cycle_section(1)

    def action_previous_section(self) -> None:
        self._cycle_section(-1)

    def action_first_section(self) -> None:
        if shelves := self._shelves():
            shelves[0].focus()

    def action_last_section(self) -> None:
        if shelves := self._shelves():
            shelves[-1].focus()

    def action_jump(self, list_id: str) -> None:
        """Go straight to a shelf by its number key.

        A no-op when that shelf is empty, rather than focusing it: an empty
        shelf is hidden, and focus on a hidden widget is focus nowhere.
        """
        with contextlib.suppress(Exception):
            shelf = self.query_one(list_id, Shelf)
            if shelf.display and shelf.cards:
                shelf.focus()

    def on_shelf_exited(self, event: Shelf.Exited) -> None:
        """Up and down leave a shelf for its neighbour."""
        self._cycle_section(event.delta)

    def on_descendant_focus(self, event) -> None:
        shelf = self._focused_shelf()
        if shelf is not None:
            self._scroll_to_shelf(shelf)
            self._paint_hero()
        self._paint_shell()

    def _scroll_to_shelf(self, shelf: Shelf) -> None:
        """Bring a shelf and its own label into view together.

        The label, not just the shelf: scrolling to the shelf alone puts its
        heading one row above the viewport, so the shelf you have just jumped to
        is the one shelf on screen that does not say what it is.
        """
        head = next((s.head_id for s in (*SECTIONS, _RESULTS_SECTION)
                     if s.list_id == f"#{shelf.id}"), None)
        target = shelf
        if head:
            with contextlib.suppress(Exception):
                target = self.query_one(head)
        with contextlib.suppress(Exception):
            self.query_one("#body", VerticalScroll).scroll_to_widget(
                target, animate=False, top=True
            )

    def _adopt_focus(self) -> None:
        """Put the cursor on the first shelf that has cards, once.

        Deliberately not done at mount, which is what the previous version got
        wrong: at mount every shelf is empty, focusing an empty container does
        not stick, and the cards arrive later from workers. The failure was
        silent, so the app launched with focus on the search box, where the
        arrow keys did nothing and no card was ever selected.
        """
        if self._focus_claimed or self._searching():
            return
        shelves = self._shelves()
        if not shelves:
            return
        self._focus_claimed = shelves[0].id
        shelves[0].focus()
        # After the refresh, not now: focus lands before Textual has laid the
        # cards out, so the hero would be drawn from a shelf that does not yet
        # report a cursor.
        self.call_after_refresh(self._paint_hero)

    # ------------------------------------------------------------------ #
    # the shell
    # ------------------------------------------------------------------ #
    def _searching(self) -> bool:
        try:
            return bool(self.query_one("#search", Input).value.strip())
        except Exception:
            return False

    def _section_label(self) -> str:
        """What the top bar calls where you are."""
        if self._searching():
            return "Search"
        shelf = self._focused_shelf()
        if shelf is None:
            return "Home"
        return next((s.label for s in (*SECTIONS, _RESULTS_SECTION)
                     if s.list_id == f"#{shelf.id}"), "Home")

    def _paint_shell(self) -> None:
        width = self.size.width or 100
        with contextlib.suppress(Exception):
            self.query_one("#topbar", TopBar).render_bar(
                self._section_label(), width,
                hint="esc close   ? help" if self._searching() else "",
            )
        # What the card under the cursor can do, named in the words the key
        # press deserves. The bar's whole reason for existing is that the one
        # row guaranteed to be visible should describe the selection rather than
        # list the same six global commands on every screen.
        contextual: list[tuple[str, str]] = []
        if (card := self._current_card()) is not None:
            contextual.append(
                ("↵", "resume" if card.resume_episode is not None else "open")
            )
        if self._searching():
            # The way out, on the one row guaranteed to be visible. Searching
            # hides every browse shelf, so without this the screen offers no
            # clue that the home screen is still there behind the results.
            contextual.append(("esc", "back"))
        else:
            contextual.append(("l", "my list"))
        with contextlib.suppress(Exception):
            # `zoom=False`: there is nothing left to expand. A shelf scrolls and
            # holds every card it was given, so the key that used to unfold a
            # capped list has no job — and a bar advertising a key the screen
            # ignores is worse than a shorter bar.
            self.query_one("#actionbar", ActionBar).render_actions(
                width, contextual=tuple(contextual), zoom=False
            )

    # ------------------------------------------------------------------ #
    # building shelves
    # ------------------------------------------------------------------ #
    async def _fill_shelf(self, sec: Section, built: list, count: int | None = None,
                          *, label: str | None = None) -> None:
        """Replace a shelf's cards, or hide it when there are none.

        ``built`` is ``(anime, caption, resume_episode, fraction)`` — the shelf
        decides what its cards say, not the card, because Continue Watching
        wants a resume percentage where This Season wants a countdown, and a
        card that chose for itself would have to know which shelf it was on.
        """
        try:
            shelf = self.query_one(sec.list_id, Shelf)
            head = self.query_one(sec.head_id, Label)
        except Exception:
            return
        await shelf.remove_children()
        if not built:
            head.display = False
            shelf.display = False
            return
        # A browse shelf stays hidden while a search is showing, and the results
        # shelf is the one that appears.
        showing = (sec is _RESULTS_SECTION) == self._searching()
        head.display = showing
        shelf.display = showing
        cards = [
            PosterCard(anime, caption, resume_episode=resume, fraction=fraction)
            for anime, caption, resume, fraction in built
        ]
        await shelf.mount_all(cards)
        shelf.index = 0
        cards[0].selected = True
        self._fill_covers(cards)
        self._set_section(sec.head_id, label or sec.label,
                          count if count is not None else len(built))
        self._adopt_focus()

    def _set_section(self, sec_id: str, base: str, count: int) -> None:
        """Draw a shelf label: letterspaced caps and the count.

        Letterspacing is what lets a label read as structure without a rule
        under it. The previous design drew `Continue Watching ───────── 18` —
        forty cells of line art carrying one integer, four times down the page.

        No "6 of 18" and no expand affordance any more: a shelf holds every card
        it was given and scrolls sideways, so there is nothing hidden for a
        second number to be honest about.
        """
        if sec_id in self._unavailable:
            return  # the shelf could not load; do not relabel it as empty
        with contextlib.suppress(Exception):
            label = self.query_one(sec_id, Label)
            tail = f"[dim]{count}[/dim]" if count else ""
            label.update(f"[b]{spaced(base)}[/b]   {tail}".rstrip())

    def _mark_section_unavailable(self, sec: Section) -> None:
        """Say a shelf could not be loaded, instead of leaving it blank.

        The failure was already announced — once, in a toast, which is gone by
        the time anyone looks. What was left behind was a heading with no count
        over empty space, and an empty "Trending" does not read as "we could not
        reach AniList", it reads as "nothing is trending".

        Learned the day AniList disabled its own public API: two shelves went
        silently empty and the app looked broken rather than blocked.
        """
        self._unavailable.add(sec.head_id)
        with contextlib.suppress(Exception):
            head = self.query_one(sec.head_id, Label)
            head.update(
                f"[b]{spaced(sec.label)}[/b]   [$warning]unavailable[/$warning]"
            )
            head.display = not self._searching()
            self.query_one(sec.list_id).display = False

    def _clear_section_unavailable(self, sec: Section) -> None:
        self._unavailable.discard(sec.head_id)

    # ------------------------------------------------------------------ #
    # AniList sync
    # ------------------------------------------------------------------ #
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
            self._load_continue()
            self._load_favorites()

    # ------------------------------------------------------------------ #
    # home data
    # ------------------------------------------------------------------ #
    @work(exclusive=True, group="continue")
    async def _load_continue(self) -> None:
        try:
            await self._continue_worker()
        except Exception as e:
            # An unhandled worker error takes the whole TUI down with a
            # traceback. A momentarily busy or damaged database must degrade to
            # an empty shelf and a message, never a crash on launch.
            self.notify(f"Couldn't load Continue Watching: {e}", severity="warning")

    async def _continue_worker(self) -> None:
        items = await self.app.services.library.continue_watching(limit=20)
        # First paint from the cached rows — a local DB read, so it is instant.
        # This is what stops Continue Watching sitting blank on launch while a
        # dozen metadata fetches run.
        await self._render_continue(items, {})
        # Then enrich with fresh airing schedules in the background and repaint:
        # that is how a caught-up airing show gets its countdown and a
        # finished-and-fully-watched show drops off.
        if items:
            fresh = await self._fresh_airing(items)
            await self._render_continue(items, fresh)

    async def _render_continue(self, items, fresh: dict) -> None:
        now = datetime.now(timezone.utc)
        built = []
        for it in items:
            anime = fresh.get(it.anime.id.anilist) or it.anime
            progress = it.progress
            if not self._continuable(anime, progress):
                continue  # finished and fully watched — nothing to continue
            self._progress[anime.id.anilist] = progress
            # Only a part-watched episode has a meaningful fraction. A card that
            # is merely "ready" sits at zero and must not draw a progress bar in
            # the hero as though it had been started.
            built.append((
                anime,
                card_caption(anime, progress, now),
                progress.next_episode,
                progress.fraction if progress.resumable else 0.0,
            ))
        # Ordered by how ready each card is to be acted on: the episode you are
        # part-way through first, then episodes waiting unwatched, then the
        # shows you are caught up on.
        built.sort(key=lambda b: 0 if b[3] > 0 else (1 if "ready" in b[1] else 2))
        await self._fill_shelf(SECTIONS[0], built, label="Continue watching")
        # A show you are already watching does not need advertising again
        # further down the page: This Season listed four of these twice, with
        # different metadata each time, which read as two different shows.
        self._continue_ids = {b[0].id.anilist for b in built}
        self._hide_seasonal_duplicates()

    @staticmethod
    def _continuable(anime, progress) -> bool:
        """Whether there is anything left to continue to.

        A *completed* episode is exactly what keeps a still-airing show in
        Continue Watching once you have finished its latest one, so "completed"
        on its own is not grounds for dropping the card — which is the mistake
        that once had `anime resume` replaying the episode just finished.
        """
        if not progress.completed:
            return True  # stopped part-way through: this is the episode to open
        if anime.is_airing:
            return True
        total = anime.episode_count
        return bool(total) and progress.episode < total

    def _hide_seasonal_duplicates(self) -> None:
        """Hide This Season cards for shows already in Continue Watching.

        Deliberately hides rather than rebuilds: both shelves are filled by
        independent workers, and clearing one from the other's worker is a
        check-then-act across an await. Setting ``display`` touches nothing the
        other worker owns.
        """
        try:
            shelf = self.query_one("#seasonal", Shelf)
        except Exception:
            return
        shown = 0
        for card in shelf.all_cards:
            card.display = card.anime.id.anilist not in self._continue_ids
            shown += card.display
        # The cursor may have been sitting on a card that has just been hidden,
        # or on an index past the end of what is left.
        shelf.reselect()
        self._set_section("#sec-seasonal", "This season", shown)

    async def _fresh_airing(self, items) -> dict:
        """Map anilist id → freshly-fetched Anime for the shows whose airing
        schedule could actually have changed.

        This used to fetch *every* Continue-Watching row at once — twenty
        concurrent AniList queries on launch, on top of seasonal, trending and
        the AniList sync. AniList rate-limits well below that, so a normal
        launch earned a 429, and because the limiter is shared the next thing
        you typed failed too: "Search failed: rate limited — try again in about
        41s".

        Almost none of those requests could return anything new:

        * a show that has finished airing has no schedule left to change;
        * a show whose cached next episode is still in the future already has
          everything the card needs — the countdown ticks locally, no network.

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
        built = [(fav.anime, card_caption(fav.anime), None, 0.0) for fav in items]
        await self._fill_shelf(SECTIONS[1], built)

    @work(exclusive=True, group="seasonal")
    async def _load_seasonal(self) -> None:
        season, year = _current_season()
        sec = SECTIONS[2]
        try:
            animes = await self.app.services.metadata.seasonal(season, year)
        except Exception as e:
            self.notify(f"Couldn't load this season: {e}", severity="warning")
            self._mark_section_unavailable(sec)
            return
        self._clear_section_unavailable(sec)
        # Soonest-airing first, so the next release to drop leads the shelf.
        far = datetime.max.replace(tzinfo=timezone.utc)
        animes = sorted(animes, key=lambda a: a.next_airing_at or far)
        built = [(a, card_caption(a), None, 0.0) for a in animes[:20]]
        await self._fill_shelf(sec, built)
        # Counts the cards that survive de-duplication, not the fetch limit.
        self._hide_seasonal_duplicates()

    @work(exclusive=True, group="trending")
    async def _load_trending(self) -> None:
        sec = SECTIONS[3]
        try:
            animes = await self.app.services.metadata.trending(limit=20)
        except Exception as e:
            self.notify(f"Couldn't load trending: {e}", severity="warning")
            self._mark_section_unavailable(sec)
            return
        self._clear_section_unavailable(sec)
        built = [(a, card_caption(a), None, 0.0) for a in animes]
        await self._fill_shelf(sec, built)

    def _tick_countdowns(self) -> None:
        """Re-cut the captions that contain a clock. No network."""
        now = datetime.now(timezone.utc)
        for card in self.query(PosterCard):
            if card.anime.is_airing:
                card.set_caption(card_caption(
                    card.anime, self._progress.get(card.anime.id.anilist), now
                ))

    # ------------------------------------------------------------------ #
    # search
    # ------------------------------------------------------------------ #
    def action_clear_search(self) -> None:
        box = self.query_one("#search", Input)
        if not box.display and not box.value:
            return
        box.value = ""
        box.display = False
        self.workers.cancel_group(self, "search")
        self._toggle_results(False)
        self._paint_shell()
        if shelves := self._shelves():
            shelves[0].focus()

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
            # a half-typed query lands *after* the box is cleared and slams
            # stale results back over the home screen.
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
        built = [(r.anime, card_caption(r.anime), None, 0.0) for r in results]
        self._toggle_results(True)
        await self._fill_shelf(_RESULTS_SECTION, built)
        self._show_no_matches(None if results else query)
        if results:
            self.query_one("#results", Shelf).focus()

    def _toggle_results(self, on: bool) -> None:
        """Swap the browse shelves for the results shelf, or back.

        A shelf that hid itself for being empty stays hidden either way: a
        search closing is not news about whether you have any favourites.
        """
        for sec in SECTIONS:
            with contextlib.suppress(Exception):
                shelf = self.query_one(sec.list_id, Shelf)
                showing = (not on) and bool(shelf.cards)
                shelf.display = showing
                self.query_one(sec.head_id).display = showing
        with contextlib.suppress(Exception):
            results = self.query_one("#results", Shelf)
            showing = on and bool(results.cards)
            results.display = showing
            self.query_one("#sec-results").display = showing
        if not on:
            # Clearing the box brings the browse shelves back; the no-matches
            # notice must not outlive the search that produced it.
            self._show_no_matches(None)
        self._paint_shell()

    def _show_no_matches(self, query: str | None) -> None:
        """Say so when a search found nothing, instead of showing bare space.

        AniList's search is strict about word boundaries, so a near-miss really
        does come back empty — and since searching hides the browse shelves, the
        result was an empty screen under a "Results" heading that gave no clue
        whether it was still loading, broken, or simply had no answer.
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
        with contextlib.suppress(Exception):
            # A heading over nothing. "Results" above an empty plate above a
            # notice saying there are none is the same fact three times, and the
            # notice is the only one of the three that says anything useful.
            self.query_one("#sec-results").display = False
            self.query_one("#results").display = False
        # The hero was still describing whichever card the cursor sat on before
        # the search — a show that is, by definition, not among the results. A
        # hero confidently detailing something the shelves do not contain is
        # worse than an empty one.
        with contextlib.suppress(Exception):
            self.query_one("#hero-art", Container).remove_children()
            self.query_one("#hero-text", Static).update(
                empty_state("Nothing to show",
                            "Pick a result to see it here.",
                            self._hero_text_width())
            )
            self._hero_id = None

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def on_screen_suspend(self) -> None:
        # A screen (detail, sources, …) was pushed over Home.
        self._was_suspended = True

    def on_screen_resume(self) -> None:
        # Only refresh after Home was actually suspended and revealed again —
        # i.e. you went into a show and came back. Whatever you watched changed
        # the library, so rebuild the library-backed shelves. Guarding on the
        # suspend avoids re-loading on the initial show (which `on_mount`
        # already did) — that double-load churned the exclusive workers.
        if getattr(self, "_was_suspended", False):
            self._was_suspended = False
            self._load_continue()
            self._load_favorites()
