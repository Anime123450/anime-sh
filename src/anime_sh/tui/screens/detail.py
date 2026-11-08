"""Detail screen: anime metadata + episode list; Enter plays an episode.

Episodes are listed from AniList's episode count — instant, no provider touched.
Providers are consulted only when the user actually plays an episode, which is
what keeps browsing fast even when scrapers are slow or down.

The show is re-fetched fresh from the metadata source on open, so a card reached
from Continue Watching or favorites (whose cached row lacks the synopsis, airing
schedule, studio and score) renders just as fully as one reached from search.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import textwrap

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, ListView, Static

from ...domain.errors import NoStreamsFound, PlayerUnavailable
from ...domain.models import Anime, Audio
from ..coverart import (
    fetch_cover,
    graphics_cover_widget,
    graphics_protocol_active,
    render_cover,
)
from ..format import (
    _human_duration,
    clean_synopsis,
    episode_air_label,
    meta_line,
    next_episode_line,
    progress_bar,
)
from ..shell import ActionBar, TopBar, spaced
from ..franchise_panel import FranchiseItem, FranchisePanel
from ..widgets import EpisodeGrid, EpisodeItem
from .confirm import ConfirmScreen
from .sources import SourceChoice, SourcesScreen

log = logging.getLogger(__name__)

# Cover width in character cells. Kept modest so the poster is a tasteful accent
# beside the metadata, not a wall of pixels — and smaller means denser, so it
# also reads a touch sharper.
_COVER_COLS = 32

# At and above this many columns the page is two columns side by side: the show
# on the left, the episodes on the right, each filling the height it is given.
# Below it they stack. 150 is where a 100-column left half still leaves the
# episodes more than forty columns, which is the narrowest a grid reads well.
_WIDE = 150
# The left column's ceiling. Beyond this the synopsis is a line of prose longer
# than anyone reads comfortably, so the surplus goes to the episodes instead.
_LEFT_MAX = 100
# The page's own side padding (see `#detail-body`), and the gap between columns.
_PAD = 2
_GAP = 2


def series_line(watched: int, total: int | None, ep_minutes: int | None = None) -> str:
    """How far through the *series* you are — and only that.

    This used to read ``0/25 · 0% · 25 left`` directly above a button saying
    ``1% watched``. Both were true (nothing finished; one episode a percent in)
    and together they read as a contradiction. The series line counts finished
    episodes in words, and the button owns the in-episode position.
    """
    if not total:
        n = f"{watched} watched" if watched else "not started"
        return f"[dim]{n}[/dim]"
    if watched >= total:
        return f"{progress_bar(1.0, 10, color='$success')}  [b $success]✓ complete[/]"
    left = total - watched
    tail = f"{left} left"
    if ep_minutes:
        tail += f" · ~{_human_duration(left * ep_minutes)}"
    return (f"{progress_bar(watched / total, 10, color='$primary')}  "
            f"[b]{watched} of {total} watched[/b] [dim]· {tail}[/dim]")


class DetailScreen(Screen):
    # The episode list is a grid now, so the cursor has to move like one. A
    # ListView cursor is linear: Down goes to the next *item*, which in a grid
    # reads as one cell to the right. Up/Down step a whole row; Left/Right step a
    # single episode.
    #
    # `h`/`l` are deliberately not bound — `l` opens My List app-wide, and
    # quietly stealing it on one screen is worse than having no vim key for
    # sideways movement.
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("n", "next_season", "Next season"),
        ("o", "watch_order", "Seasons & related"),
        # Choosing a source is something you do on purpose, from here, rather than
        # a page every show has to pass through on the way in. Playback fans out
        # across the providers on its own until you pin one.
        ("s", "pick_source", "Source"),
        ("w", "mark_watched", "Mark watched"),
        Binding("tab", "toggle_focus", "Switch list", show=False),
        Binding("j", "grid_down", "Down", show=False),
        Binding("k", "grid_up", "Up", show=False),
        Binding("right", "grid_next", "Next episode", show=False),
        Binding("left", "grid_prev", "Previous episode", show=False),
        # `v` for view, not `g`: `g` is "first row" app-wide, and quietly
        # stealing a key on one screen is worse than picking a duller letter —
        # the same reason `h`/`l` are left alone above.
        ("v", "cycle_layout", "Layout"),
    ]

    def _move_episode(self, delta: int) -> None:
        try:
            lv = self.query_one("#episodes", EpisodeGrid)
        except Exception:
            return
        count = len(lv.children)
        if not count or lv.index is None:
            return
        # Clamped rather than wrapped: rolling from episode 28 back to 1 on a
        # stray keypress is disorienting in a grid, where it throws the cursor
        # corner to corner instead of moving it a step.
        lv.index = max(0, min(count - 1, lv.index + delta))

    def action_grid_down(self) -> None:
        self._move_episode(self._episode_columns())

    def action_grid_up(self) -> None:
        self._move_episode(-self._episode_columns())

    def action_grid_next(self) -> None:
        self._move_episode(1)

    def action_grid_prev(self) -> None:
        self._move_episode(-1)

    # Two arrangements of the same four parts. Wide: the show on the left and
    # the episodes on the right, each as tall as the screen, so neither scrolls
    # the other away. Narrow: stacked, with the episodes taking whatever height
    # the show leaves. The page itself only scrolls when the terminal is too
    # short to hold the show and a handful of episodes (see `#detail-right`).
    # This is `CSS`, not `DEFAULT_CSS`: app.tcss gives every ListView a ten-row
    # cap and every `#episodes` a margin, and default rules always lose to it.
    CSS = """
    DetailScreen #detail-body { width: 100%; height: 1fr; padding: 0 2; }
    DetailScreen #detail-left { width: 100%; height: auto; }
    DetailScreen #detail-top { width: 100%; height: auto; }
    DetailScreen #detail-cover { width: 34; height: auto; padding: 0 2 0 0; }
    DetailScreen #detail-info { width: 1fr; height: auto; }
    DetailScreen #detail-meta { width: 100%; height: auto; padding: 0 0 1 0; }
    DetailScreen #detail-progress { height: auto; }
    DetailScreen #detail-action { height: auto; padding: 1 0 0 0; }
    DetailScreen #detail-right { width: 100%; height: 1fr; min-height: 8; }
    DetailScreen #detail-meta { background: $background; margin: 0; }
    DetailScreen #episodes { height: 1fr; max-height: 100000; margin: 0;
                             padding: 0; background: $surface; }
    DetailScreen #sec-episodes { margin: 0 0 0 0; }
    DetailScreen #episode-detail { height: auto; padding: 0 0 0 1; }
    DetailScreen #franchise-list { height: 1fr; max-height: 100000; margin: 0; }
    DetailScreen.-wide #detail-body { layout: horizontal; overflow: hidden hidden;
                                      padding: 1 2 0 2; }
    DetailScreen.-wide #detail-left { height: 100%; overflow-y: auto; }
    DetailScreen.-wide #detail-right { width: 1fr; height: 100%; min-height: 0;
                                       margin: 0 0 0 2; }
    """

    #: Episode cells mounted per yield to the event loop. Mounting is what
    #: costs — 1175 ONE PIECE cells in one go froze the screen for 5.7 seconds.
    #: This size puts the first cells up in about a third of a second and keeps
    #: the worst uninterruptible stretch well under one.
    _RENDER_CHUNK = 120

    def __init__(self, anime: Anime, *, resume_episode: float | None = None,
                 source=None, audio: Audio | None = None) -> None:
        super().__init__()
        self.anime = anime
        self.resume_episode = resume_episode
        self.source = source  # a chosen SourceOption, or None to fan out
        self.audio = audio  # local fallback; None means app playback preference
        self._numbers: list[float] = []
        self._available: set[float] | None = None
        # _render_episodes is called from several worker groups (episodes, play,
        # marks); serialize them so a clear+append from one can't interleave with
        # another and double the list (seen after a series auto-completes).
        self._render_lock = asyncio.Lock()
        # Which render is current. A render in flight stops as soon as a newer
        # one arrives, so a long grid is never mounted twice over.
        self._render_gen = 0
        self._layout = self._configured_layout()

    @staticmethod
    def _configured_layout() -> str:
        """The episode layout from config, or the default if it cannot be read.

        Best-effort on purpose: an unreadable config file should cost you your
        preferred arrangement, not the screen.
        """
        from ...layout_names import DEFAULT_EPISODE_LAYOUT, EPISODE_LAYOUTS

        try:
            from ...config import load_config

            value = load_config().ui.episodes
        except Exception:
            return DEFAULT_EPISODE_LAYOUT
        return value if value in EPISODE_LAYOUTS else DEFAULT_EPISODE_LAYOUT

    def apply_setting(self, key: str, value) -> None:
        """Settings saved a value; re-lay the episodes if it was the layout."""
        if key == "ui.episodes" and value != self._layout:
            self._layout = str(value)
            if self._numbers:
                self._relayout_episodes()

    def action_cycle_layout(self) -> None:
        """Step to the next episode layout and remember it.

        Switching costs a full re-render, which is why it is a keypress rather
        than something that happens while you scroll: you choose an arrangement
        once and it stays chosen, across screens and across runs.
        """
        from ...layout_names import EPISODE_LAYOUTS

        nxt = EPISODE_LAYOUTS[
            (EPISODE_LAYOUTS.index(self._layout) + 1) % len(EPISODE_LAYOUTS)
        ]
        self._layout = nxt
        try:
            from ...config import set_config_value

            set_config_value("ui.episodes", nxt)
        except Exception as e:
            self.notify(f"Couldn't save the layout: {e}", severity="warning")
        self.notify(f"Episode layout: {nxt}")
        if self._numbers:
            self._relayout_episodes()

    @work(exclusive=True, group="episodes")
    async def _relayout_episodes(self) -> None:
        await self._render_episodes(self._numbers, available=self._available)

    def compose(self) -> ComposeResult:
        # The same two bars the home screen wears, for the same two reasons: the
        # top one says where you are, the bottom one says what the thing under
        # the cursor can do. This screen was the last one still carrying
        # Textual's `Header` and `Footer` — a centred title with a clock over a
        # row of nine global keys, in the same order whatever was selected. It
        # was also, not coincidentally, the only screen that still looked like
        # the app before the redesign.
        yield TopBar(id="topbar")
        with VerticalScroll(id="detail-body"):
            with Vertical(id="detail-left"):
                with Horizontal(id="detail-top"):
                    yield Container(id="detail-cover")
                    # Poster-first: everything about the show, including how far
                    # you are and what Enter will play, sits beside its poster.
                    with Vertical(id="detail-info"):
                        yield Static(self._header_text(), id="detail-meta")
                        yield Static("", id="detail-progress")
                        yield Static("", id="detail-action")
                yield FranchisePanel(id="detail-franchise")
            with Vertical(id="detail-right"):
                yield Label(spaced("Episodes"), classes="shelf-label",
                            id="sec-episodes")
                yield EpisodeGrid(id="episodes")
                # What the cell cannot say. The grid carries state and number;
                # this line carries the sentence for whichever episode the
                # cursor is on.
                yield Static("", id="episode-detail")
        yield ActionBar(id="actionbar")

    # A cell is glyph + space + number, plus one column of gutter either side.
    _CELL_GAP = 3

    def _cell_width(self) -> int:
        """One episode cell: glyph, space, the widest number, and its gutter.

        `compact` closes the gutter to a single column. On a series with four
        figures of episodes that is the difference between fifteen across and
        twenty-one, which is six fewer rows for every hundred episodes.
        """
        digits = len(f"{max(self._numbers, default=1):g}")
        gap = 1 if self._layout == "compact" else self._CELL_GAP
        return 3 + digits + gap

    def _episode_columns(self) -> int:
        """How many episodes fit across.

        Not capped at twelve any more. The cap did not make the grid twelve
        cells wide — it made it twelve *columns*, and a Textual grid divides the
        width it is given between however many columns it has. On a 190-column
        terminal that meant twelve columns fifteen cells wide holding five cells
        of content each: episode 1 and episode 2 sat fourteen spaces apart, and
        a twelve-episode season was strung out across the whole window with the
        numbers floating in the gaps.

        Taking every column that fits is what makes the cells sit next to each
        other. It also costs a long-runner far fewer rows: ONE PIECE's 1175
        episodes come out at 23 across rather than 12.
        """
        if self._layout == "list":
            return 1  # one episode per line; the cursor steps one row at a time
        # The right-hand column's width in wide mode, the page's in narrow —
        # never the screen's: on a 190-column terminal the grid has 84 of them.
        usable = max(20, self._geometry()[2] - 3)
        return max(1, usable // self._cell_width())

    def _geometry(self, width: int | None = None) -> tuple[bool, int, int]:
        """``(wide, left_width, episodes_width)`` for a terminal this wide.

        Pure arithmetic on the terminal's width rather than a read of the
        widgets: it is needed before the first layout pass has given them a
        size, and again on every resize.
        """
        width = width or self.size.width or 100
        inner = width - 2 * _PAD
        if width < _WIDE:
            return False, inner, inner
        left = min(_LEFT_MAX, int(width * 0.55))
        return True, left, inner - left - _GAP

    def _apply_layout(self, width: int, height: int) -> None:
        """Switch between the two arrangements and size the left column."""
        wide, left, _ = self._geometry(width)
        self.set_class(wide, "-wide")
        with contextlib.suppress(Exception):
            self.query_one("#detail-left").styles.width = left if wide else "100%"
        with contextlib.suppress(Exception):
            self.query_one("#detail-franchise", FranchisePanel).display = wide
        self._layout_size = (width, height)
        self._refresh_header()

    def _info_width(self) -> int:
        """Columns the metadata panel has beside the poster."""
        width, height = getattr(self, "_layout_size", None) or (
            self.size.width or 100, self.size.height or 40)
        wide, left, _ = self._geometry(width)
        cover = 0 if getattr(self, "_cover_hidden", False) else (
            self._cover_cols(width, height) + 2)
        return max(24, (left if wide else width - 2 * _PAD) - cover)

    def _synopsis_lines(self) -> int:
        """How many lines of synopsis the layout can afford."""
        width, height = getattr(self, "_layout_size", None) or (
            self.size.width or 100, self.size.height or 40)
        if width >= _WIDE:
            return 11
        return 8 if height >= 40 else 6 if height >= 32 else 3

    def _size_episode_grid(self) -> None:
        try:
            lv = self.query_one("#episodes", EpisodeGrid)
        except Exception:
            return
        columns = self._episode_columns()
        # The list layout already spells out every episode's state in its own row,
        # so the sentence under the grid would just say the highlighted row twice.
        with contextlib.suppress(Exception):
            self.query_one("#episode-detail", Static).display = self._layout != "list"
        # The class is what narrows the row padding; `_cell_width` narrows the
        # column to match. Setting one without the other clips the numbers.
        lv.set_class(self._layout == "compact", "-compact")
        if self._layout == "list":
            # Not a grid at all: one full-width row per episode, which is what
            # the list layout is for.
            lv.styles.layout = "vertical"
            lv.columns = 1
            return
        lv.styles.layout = "grid"
        lv.styles.grid_size_columns = columns
        # One line per row. In a list that fills the column, rows would otherwise
        # share its whole height between them and drift apart.
        lv.styles.grid_rows = "1"
        # Fixed, not fractional. Without this the grid shares its whole width
        # out between the columns, so the cells stretch and the numbers drift
        # apart instead of forming a block you can read as one shape.
        lv.styles.grid_columns = str(self._cell_width())
        lv.columns = columns  # so its cursor steps a whole row, not one cell

    def on_list_view_highlighted(self, event) -> None:
        """Describe the highlighted episode underneath the grid."""
        item = event.item
        if isinstance(item, EpisodeItem):
            try:
                self.query_one("#episode-detail", Static).update(item.detail)
            except Exception as e:
                log.debug("failed to update highlighted episode detail: %s", e)

    def on_mount(self) -> None:
        # Still set, though no `Header` reads them any more: Textual puts them on
        # the terminal's own window title, which is where an application's name
        # belongs and the reason the top bar does not need to repeat it.
        self.title = self.anime.title.preferred
        if self.anime.episode_count:
            self.sub_title = f"{self.anime.episode_count} episodes planned"
        self._apply_layout(self.size.width or 100, self.size.height or 40)
        self._paint_shell()
        self._load_cover()
        self._populate_episodes()
        self._load_franchise()

    def _load_franchise(self) -> None:
        self._load_franchise_worker()

    @work(exclusive=True, group="franchise")
    async def _load_franchise_worker(self) -> None:
        svc = getattr(self.app.services, "franchise", None)
        if not svc or not hasattr(svc, "build"):
            return
        try:
            f = await svc.build(self.anime)
            if not f:
                return
            try:
                fp = self.query_one("#detail-franchise", FranchisePanel)
                fp.set_franchise(f)
            except Exception:
                pass
        except Exception as e:
            log.debug("could not load franchise: %s", e)

    def action_toggle_focus(self) -> None:
        try:
            ep_lv = self.query_one("#episodes", ListView)
            fp_lv = self.query_one("#franchise-list", ListView)
        except Exception:
            return
        if not fp_lv.display and not self.has_class("-wide"):
            return
        if fp_lv.has_focus:
            ep_lv.focus()
        else:
            fp_lv.focus()

    def _paint_shell(self) -> None:
        """Repaint the two bars: where you are, and what you can do from here."""
        width = self.size.width or 100
        with contextlib.suppress(Exception):
            # The title goes in the bar and *only* in the bar. It used to be in
            # the header and again as the first line of the metadata panel —
            # the longest string on the screen, twice, eight rows apart.
            self.query_one("#topbar", TopBar).render_bar(
                self.anime.title.preferred, width, hint=self.sub_title or "")
        with contextlib.suppress(Exception):
            # What this screen can do, in the order you are likely to want it.
            # `z` is deliberately absent: the detail screen does not bind it.
            self.query_one("#actionbar", ActionBar).render_actions(
                width, zoom=False,
                contextual=(("↵", "play"), ("esc", "back"),
                            ("s", "source"), ("w", "mark watched"),
                            ("n", "next season"),
                            ("o", "order"), ("v", "layout")))

    @work(exclusive=True, group="cover")
    async def _load_cover(self) -> None:
        # Cover art is pure decoration: fetch + render off the main path, and
        # never surface a failure (no Pillow, offline, odd image → just skip).
        url = self.anime.cover_url
        if not url:
            self._collapse_cover()
            return
        try:
            data = await fetch_cover(url)
            if not data:
                self._collapse_cover()
                return
            self._cover_data = data
            await self._mount_cover()
        except Exception:
            # decoration only — a broken image never costs you the screen, but
            # it must not cost you the column it was going to sit in either.
            self._collapse_cover()

    def _collapse_cover(self) -> None:
        """Take the poster's column back when there is going to be no poster.

        `#detail-cover` is a fixed 34 cells, reserved before the fetch so the
        metadata does not jump sideways when the art lands. Every way the fetch
        can fail — no cover URL, an unreachable host, no Pillow to decode with —
        returned early and left that reservation standing, so the panel sat
        indented past an empty gutter a third of the screen wide with nothing
        in it.

        Collapsed only once there is definitely nothing to show, so the common
        case still has its space held and still does not jump.
        """
        self._cover_hidden = True
        try:
            self.query_one("#detail-cover", Container).display = False
        except Exception as e:
            log.debug("could not collapse missing cover container: %s", e)
        self._refresh_header()

    def _cover_cols(self, width: int | None = None, height: int | None = None) -> int:
        """Poster width for this terminal.

        A full-size poster is about 23 rows tall. Stacked (narrow), that is half
        of a 49-row window before a single episode, so the poster only goes full
        size when the window is tall enough to keep a useful list beneath it;
        side by side (wide) it costs the list nothing, so any window of 32 rows
        or more gets the full one.
        """
        size = getattr(self, "_layout_size", None) or (self.size.width, self.size.height)
        width = width or size[0] or 100
        height = height or size[1] or 40
        if width >= _WIDE:
            return _COVER_COLS if height >= 32 else 18
        return _COVER_COLS if height >= 44 else 18

    async def _mount_cover(self) -> None:
        data = getattr(self, "_cover_data", None)
        if not data:
            return
        try:
            container = self.query_one("#detail-cover", Container)
        except Exception:
            return
        await container.remove_children()
        cols = self._cover_cols()
        # The stylesheet reserves the full-size column; a short terminal gets
        # a narrower one so the column matches the smaller poster.
        container.styles.width = cols + (34 - _COVER_COLS)
        # Prefer a true-bitmap render (Sixel/kitty) when the terminal supports
        # it — crisp, unlike the unicode-block fallback. Any failure drops back
        # to the block render so the cover still shows.
        if graphics_protocol_active():
            widget = graphics_cover_widget(data, cols)
            if widget is not None:
                try:
                    await container.mount(widget)
                    return
                except Exception as e:
                    log.debug("bitmap cover widget mount failed: %s", e)
        art = render_cover(data, cols=cols)
        if art is None:
            # No Pillow, or an image it could not decode. Nothing will ever be
            # mounted here, so the column is dead weight.
            self._collapse_cover()
            return
        try:
            await container.mount(Static(art))
        except Exception:
            self._collapse_cover()

    def on_resize(self, event) -> None:
        # Grid columns and shell bars depend on the terminal width. Keep both
        # live in the one handler Python actually dispatches. A Sixel image
        # does not reflow, so remount it only when the size itself changes.
        self._apply_layout(event.size.width, event.size.height)
        self._size_episode_grid()
        self._paint_shell()
        size = (event.size.width, event.size.height)
        if size == getattr(self, "_cover_size", None):
            return
        self._cover_size = size
        if getattr(self, "_cover_data", None):
            self._remount_cover()

    @work(exclusive=True, group="cover")
    async def _remount_cover(self) -> None:
        await self._mount_cover()

    @work(exclusive=True, group="episodes")
    async def _populate_episodes(self) -> None:
        try:
            await self._populate_episodes_worker()
        except Exception as e:
            # Same reasoning as Home's Continue Watching: a provider or database
            # hiccup here must show a message, not crash out of the app.
            self.notify(f"Couldn't load episodes: {e}", severity="warning")

    async def _populate_episodes_worker(self) -> None:
        # One serialized pass so the three sources never race each other:
        #   1. instant render from AniList's planned count (+ watched marks)
        #   2. enrich to full, fresh metadata (synopsis / airing / studio / score)
        #   3. refine to what the provider actually has, with air countdowns
        await self._load_marks()
        count = self.anime.episode_count or 0
        await self._render_episodes([float(n) for n in range(1, count + 1)] or [1.0])

        fresh = await self._fresh_metadata()
        if fresh is not None:
            self.anime = fresh
            self._refresh_header()
            self._refresh_progress()  # fresh metadata may fill in the total

        available = await self._available_episodes()
        planned = self.anime.episode_count or 0
        numbers = sorted({float(n) for n in range(1, planned + 1)} | set(available or []))
        await self._render_episodes(
            numbers or [1.0],
            available=set(available) if available else None,
        )
        self._refresh_subtitle(available)

    async def _fresh_metadata(self) -> Anime | None:
        """Re-fetch the show from the metadata source (cached ~1h) so every
        card renders full detail, not just what a cached row happened to keep.
        Best-effort: None if the source can't be reached or lacks ``get``."""
        get = getattr(self.app.services.metadata, "get", None)
        if get is None or self.anime.id.anilist is None:
            return None
        try:
            return await get(self.anime.id)
        except Exception:
            return None

    async def _available_episodes(self) -> list[float] | None:
        try:
            return await self.app.services.playback.available_episodes(
                self.anime,
                audio=self.audio or getattr(self.app, "audio", Audio.SUB),
                source=self.source,
            )
        except Exception:
            return None

    async def _load_marks(self) -> None:
        """Compute the watch state from the library.

        Watching is linear, so the *furthest completed episode* implies every
        earlier one is watched too — that's what makes a show whose progress was
        synced from AniList (one "watched up to N" row) light up episodes 1..N,
        not just N. In-progress percents past that mark are kept; ones at or
        below it are stale (superseded by a later completion) and dropped."""
        self._watched_through: float = 0
        self._partial: dict[float, int] = {}
        try:
            for p in await self.app.services.library.progress_for(self.anime.id):
                if p.completed:
                    self._watched_through = max(self._watched_through, p.episode)
                elif p.position_s > 0 and p.duration_s > 0:
                    self._partial[p.episode] = round(p.fraction * 100)
        except Exception:
            pass  # marks are decoration; never block the episode list
        self._partial = {
            e: pct for e, pct in self._partial.items() if e > self._watched_through
        }
        self._refresh_progress()

    def refresh_marks(self) -> None:
        """Re-read watch progress and repaint. Driven by playback events so an
        episode finishing mid auto-next updates its ✓ live, not only when the
        whole auto-next run ends. (These repaints only happen during playback,
        when you're watching mpv — normal browsing stays flicker-free.)"""
        self._refresh_marks_worker()

    @work(exclusive=True, group="marks")
    async def _refresh_marks_worker(self) -> None:
        try:
            await self._load_marks()
            if self._numbers:
                await self._render_episodes(self._numbers, available=self._available)
        except Exception:
            return  # a refresh that fails leaves the marks as they were
        try:
            self.app.refresh(repaint=True)
        except Exception as e:
            log.debug("TUI repaint after marks refresh failed: %s", e)

    def _refresh_progress(self) -> None:
        """Update the overall watch-progress bar under the header."""
        try:
            self.query_one("#detail-progress", Static).update(
                series_line(int(getattr(self, "_watched_through", 0)),
                            self.anime.episode_count,
                            ep_minutes=self.anime.duration_min)
            )
        except Exception:
            pass

    def _unavailable_label(self, number: float) -> str:
        """Why an episode can't be played right now.

        Two different reasons used to share one wrong label: an episode that
        hasn't aired, and an episode this *source* simply doesn't carry. A
        finished season whose chosen source stops at episode 4 would say its
        remaining episodes were "not aired yet" — years after they aired. The
        fix for that one is to switch source, so say so.
        """
        airing = episode_air_label(self.anime, number)
        if airing:
            return airing
        aired_through = self.anime.aired_through()
        if aired_through is not None and number <= aired_through:
            return "aired · not on this source — press s to pick another"
        if self.anime.is_airing:
            return "not aired yet"
        return "not on this source — press s to pick another"

    async def _render_episodes(
        self, numbers: list[float], available: set[float] | None = None
    ) -> None:
        self._numbers = numbers
        self._available = available
        watched_through = getattr(self, "_watched_through", 0)
        partial = getattr(self, "_partial", {})

        def is_avail(n: float) -> bool:
            return available is None or n in available

        # Next to watch: an explicit resume target — but only until you've
        # actually finished it (after that the pin is stale and the cursor should
        # roll on), then the in-progress episode, then the first available one
        # past the watched-through mark.
        resume = self.resume_episode
        # Drop the pin once you've finished that episode, and also when the
        # chosen source doesn't actually carry it — otherwise the screen offers
        # "Play Episode 5" (and parks the cursor there) for an episode this
        # source hasn't got, and pressing Enter can only fail.
        if resume is not None and (resume <= watched_through or not is_avail(resume)):
            resume = None
        next_number = (
            resume
            if resume is not None
            else min(partial) if partial
            else next((n for n in numbers if n > watched_through and is_avail(n)), None)
        )
        # Serialize the clear+append: without this, a concurrent render (play /
        # marks / episodes workers all call here) can interleave — one clears, the
        # other clears, both append — and the list ends up doubled.
        # Which episodes are already on disk. Asked of the playback service so
        # the mark and the thing that actually plays agree; a local read, so no
        # network and nothing to await per episode.
        playback = getattr(self.app.services, "playback", None)
        downloaded = set()
        if playback is not None and hasattr(playback, "is_downloaded"):
            downloaded = {
                n for n in numbers if playback.is_downloaded(self.anime, n)
            }

        # Bumped outside the lock on purpose. A later render must be able to tell
        # an in-flight one to stop *while* it is still holding the lock —
        # otherwise the newer call queues behind a long fill, and the screen
        # mounts a thousand cells only to clear them and mount them again.
        self._render_gen += 1
        gen = self._render_gen

        width = len(f"{max(numbers, default=1):g}")
        select_index = 0
        items: list[EpisodeItem] = []
        for i, number in enumerate(numbers):
            avail = is_avail(number)
            is_watched = number <= watched_through
            pct = partial.get(number)
            if number == next_number:
                select_index = i
            items.append(
                EpisodeItem(
                    number,
                    width=width,
                    layout=self._layout,
                    downloaded=number in downloaded,
                    watched=is_watched,
                    resume_s=1 if number == resume else 0,
                    progress_pct=pct,
                    available=avail,
                    air_label=None if avail else self._unavailable_label(number),
                    is_next=(not is_watched and pct is None
                             and number == next_number and avail),
                )
            )

        async with self._render_lock:
            if gen != self._render_gen:
                return
            lv = self.query_one("#episodes", EpisodeGrid)
            await lv.clear()
            # Mounted in chunks, yielding between them. Building the cells is
            # nearly free (0.09s for ONE PIECE's 1175); *mounting* them is what
            # costs, and doing it in one go froze the screen for 5.7 seconds on
            # a long-running show — twice, because this runs again once the
            # provider's episode list arrives. In chunks the first cells are up
            # in 0.35s and the screen stays answerable while the rest fill in.
            placed = False
            for start in range(0, len(items), self._RENDER_CHUNK):
                if gen != self._render_gen:
                    return  # a newer render is waiting; stop wasting mounts
                lv.extend(items[start:start + self._RENDER_CHUNK])
                if not placed and select_index < start + self._RENDER_CHUNK:
                    # As soon as the cursor's own cell exists, put the cursor on
                    # it — waiting for the whole grid would leave the episode you
                    # came here to play unselected for seconds.
                    self._size_episode_grid()
                    lv.index = select_index
                    self._focus_grid(lv)
                    placed = True
                if start + self._RENDER_CHUNK < len(items):
                    await asyncio.sleep(0)
            if not placed:
                self._size_episode_grid()
                lv.index = select_index
                self._focus_grid(lv)
            else:
                # The column count depends on the widest episode number, which
                # is only certain once every cell is mounted.
                self._size_episode_grid()
        self._refresh_action(next_number, partial, watched_through)

    def _refresh_action(self, next_number, partial: dict, watched_through) -> None:
        """The one-line call-to-action above the episode list: what pressing
        Enter will do — resume, start, play next, or a done note."""
        total = self.anime.episode_count
        if next_number is None:
            if total and watched_through >= total:
                text = "[b $background on $success] ✓ finished [/] [$text-muted]You've watched every episode.[/]"
            elif self._available is not None and self._available:
                # Nothing left to play *here*, but the series isn't finished —
                # this source just stops earlier than the show does. Silence
                # looked like a bug; the fix is one keypress away.
                text = ("[$warning]This source has no further episodes.[/] "
                        "[dim]Press s to pick another source.[/dim]")
            else:
                text = ""
        else:
            n = f"{next_number:g}"
            pct = partial.get(next_number)
            # A filled bar, the same one the home hero uses. As cyan-on-nothing
            # it was the loudest *text* on the screen but still read as a label,
            # so a reader could see a resume percentage and have nothing telling
            # them how to act on it. The "press Enter" is dropped: the action bar
            # at the foot now says it, and saying it twice on one screen was the
            # density this redesign is about.
            verb = "resume" if pct else ("start" if watched_through <= 0 else "play")
            # The percentage is *this episode's* position and lives in the
            # button, where it can only be read as what Enter will resume. The
            # series line above counts finished episodes and says nothing of it.
            tail = f" · {pct}%" if pct else ""
            text = f"[b $background on $accent] ▶  {verb} episode {n}{tail} [/]"
        try:
            self.query_one("#detail-action", Static).update(text)
        except Exception:
            pass

    def _refresh_subtitle(self, available: list[float] | None) -> None:
        if available:
            src = f" · {self.source.provider}" if self.source else ""
            planned = self.anime.episode_count
            of = f"/{planned}" if planned else ""
            self.sub_title = f"{len(available)}{of} episodes available{src}"
            # The bar is painted once on mount, before any source has answered,
            # so without this it kept saying "12 episodes planned" long after it
            # knew how many were actually there.
            self._paint_shell()

    def _refresh_header(self) -> None:
        try:
            self.query_one("#detail-meta", Static).update(self._header_text())
        except Exception:
            pass
        self.title = self.anime.title.preferred

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, EpisodeItem):
            if not item.available:
                self.notify(
                    f"Episode {item.number:g} isn't available yet.",
                    severity="warning",
                )
                return
            self._play(item.number)
        elif isinstance(item, FranchiseItem):
            self.app.push_screen(DetailScreen(item.entry.anime))

    def action_mark_watched(self) -> None:
        try:
            fp_lv = self.query_one("#franchise-list", ListView)
        except Exception:
            fp_lv = None

        if fp_lv is not None and fp_lv.has_focus:
            highlighted = fp_lv.highlighted_child
            if not isinstance(highlighted, FranchiseItem):
                return
            entry = highlighted.entry
            title = entry.anime.title.preferred

            def on_confirm_fr(confirmed: bool) -> None:
                if confirmed:
                    self._mark_franchise_entry(entry.anime)

            self.app.push_screen(
                ConfirmScreen(
                    f"Mark all of [b]{title}[/] as watched elsewhere?",
                    title="Mark Watched",
                ),
                on_confirm_fr,
            )
            return

        try:
            ep_lv = self.query_one("#episodes", EpisodeGrid)
        except Exception:
            try:
                ep_lv = self.query_one("#episodes", ListView)
            except Exception:
                return

        highlighted = ep_lv.highlighted_child
        if isinstance(highlighted, EpisodeItem):
            num = float(highlighted.number)
        elif ep_lv.index is not None and 0 <= ep_lv.index < len(self._numbers):
            num = float(self._numbers[ep_lv.index])
        elif ep_lv.index is not None:
            num = float(ep_lv.index + 1)
        else:
            return

        def on_confirm_ep(confirmed: bool) -> None:
            if confirmed:
                self._mark_episodes_up_to(num)

        self.app.push_screen(
            ConfirmScreen(
                f"Mark up to Episode {num:g} as watched?",
                title="Mark Watched",
            ),
            on_confirm_ep,
        )

    def _mark_franchise_entry(self, anime: Anime) -> None:
        self._mark_franchise_entry_worker(anime)

    @work(exclusive=True, group="franchise_mark")
    async def _mark_franchise_entry_worker(self, anime: Anime) -> None:
        svc = getattr(self.app.services, "franchise", None)
        if svc and hasattr(svc, "mark_entry_watched"):
            try:
                await svc.mark_entry_watched(anime)
            except Exception as e:
                self.notify(f"Could not mark entry watched: {e}", severity="error")
                return
        self.notify(f"Marked {anime.title.preferred} as watched")
        self._load_franchise()
        if anime.id == self.anime.id:
            self.refresh_marks()

    def _mark_episodes_up_to(self, num: float) -> None:
        self._mark_episodes_up_to_worker(num)

    @work(exclusive=True, group="marks")
    async def _mark_episodes_up_to_worker(self, num: float) -> None:
        lib = getattr(self.app.services, "library", None)
        if lib and hasattr(lib, "mark_watched"):
            try:
                await lib.mark_watched(self.anime, num)
            except Exception as e:
                self.notify(f"Could not mark episode: {e}", severity="error")
                return
        self.notify(f"Marked up to Episode {num:g} as watched")
        await self._load_marks()
        self._refresh_header()
        if self._numbers:
            await self._render_episodes(self._numbers, available=self._available)

    @work(exclusive=True, group="play")
    async def _play(self, number: float) -> None:
        total = self.anime.episode_count
        label = f"{number:g}/{total}" if total else f"{number:g}"
        self.notify(f"Resolving Episode {label}…", timeout=4)
        try:
            await self.app.services.playback.play_and_track(
                self.anime, number,
                # Was hardcoded SUB, which quietly ignored `playback.audio` for
                # everyone watching from the TUI rather than the CLI.
                audio=self.audio or getattr(self.app, "audio", Audio.SUB),
                source=self.source,
            )
        except NoStreamsFound as e:
            # The service distinguishes "this source doesn't list the episode"
            # from "it listed it but nothing would play" — surface that instead
            # of flattening both into a generic miss, which hides the real cause.
            hint = " Press s to pick another source." if self.source else ""
            self.notify(f"Episode {number:g}: {e}{hint}", severity="error")
            if self._effective_audio() is Audio.DUB and self.source is None:
                # No dub anywhere. The picker is where "Switch to Sub for this
                # title only" lives, so open it rather than leave a dead end.
                self.action_pick_source()
        except PlayerUnavailable as e:
            if "vlc" in str(e).lower():
                # VLC was chosen in Settings and isn't where we looked: the
                # error already names the fix (install it, set the path, or
                # switch back to mpv). Never fall back to mpv silently.
                self.notify(f"{e}\nChange it in Settings (,) → Playback.",
                            title="VLC not found", severity="error", timeout=15)
            else:
                # The single most common first-run wall: no mpv. Say exactly how to fix it.
                self.notify(
                    "mpv isn't installed — that's the video player anime-sh uses.\n"
                    "Windows: scoop install mpv   ·   macOS: brew install mpv\n"
                    "Linux: sudo apt install mpv (or your package manager).\n"
                    "Then run `anime doctor` to confirm.",
                    title="Player not found", severity="error", timeout=15,
                )
        except Exception as e:  # keep the TUI alive on any playback failure
            self.notify(f"Playback error: {e}", severity="error")
        # Playback (and any auto-next) is done and progress is saved — refresh
        # the ✓ / ▸ marks in place so the list reflects what you just watched.
        # Guarded: the episode already played, so a hiccup reading it back must
        # not crash the screen you're returning to.
        try:
            await self._load_marks()
            if self._numbers:
                await self._render_episodes(self._numbers, available=self._available)
        except Exception:
            pass
        # mpv plays in its own window while Textual sits idle in the background,
        # so it doesn't repaint on its own when mpv closes — the updated marks
        # would otherwise only show after leaving and re-opening the screen.
        try:
            self.app.refresh(repaint=True)
        except Exception:
            pass

    def _effective_audio(self) -> Audio:
        return self.audio or getattr(self.app, "audio", Audio.SUB)

    def action_pick_source(self) -> None:
        """Open the source picker for this show.

        Optional and explicit: nothing sends you here on the way in. Choosing a
        row pins it on *this* screen and refreshes what is available; Esc leaves
        everything as it was.
        """
        self.app.push_screen(
            SourcesScreen(self.anime, audio=self._effective_audio(), pick=True),
            self._source_chosen,
        )

    def _source_chosen(self, choice: SourceChoice | None) -> None:
        if choice is None:
            return
        self.source = choice.source
        self.audio = choice.audio
        where = choice.source.provider if choice.source else "any provider"
        self.notify(f"Source: {where}")
        self._populate_episodes()  # availability depends on the source

    def action_watch_order(self) -> None:
        from .watch_order import WatchOrderScreen

        self.app.push_screen(WatchOrderScreen(self.anime))

    def action_next_season(self) -> None:
        self._open_next_season()

    @work(exclusive=True, group="sequel")
    async def _open_next_season(self) -> None:
        try:
            sequel = await self.app.services.metadata.sequel(self.anime.id)
        except Exception:
            sequel = None
        if sequel is None:
            self.notify("No next season found.", severity="warning")
            return
        self.notify(f"Next season: {sequel.title.preferred}")
        self.app.push_screen(DetailScreen(sequel))

    def _focus_grid(self, lv) -> None:
        """Focus the episode grid without throwing away the top of the screen.

        A plain `focus()` scrolls the grid into view, and on a short terminal
        that pushed the poster, the title and the synopsis off the top before
        you had read any of them. The page now opens at the top. The play
        button already names the episode Enter will start, and moving the
        cursor scrolls as usual.
        """
        lv.focus(scroll_visible=False)
        with contextlib.suppress(Exception):
            self.query_one("#detail-body").scroll_home(animate=False)

    def _header_text(self) -> str:
        a = self.anime
        # No title. The top bar carries it, and this panel used to repeat it as
        # its first line — the longest string on the screen, printed twice,
        # eight rows apart, which is what made the screen read as two stacked
        # headers rather than one heading and its details.
        lines = []
        # The alternate title stays: when English and romaji differ it is how
        # you confirm it is the right show, and the bar only has room for one.
        alt = a.title.romaji if a.title.english and a.title.romaji else None
        if alt and alt != a.title.preferred:
            lines.append(f"[dim]{alt}[/dim]")
        lines.append(meta_line(a))
        nxt = next_episode_line(a)
        if nxt:
            lines.append(f"[b $background on $success] ● next [/] {nxt}")
        if a.genres:
            lines.append(f"[$secondary]{' · '.join(a.genres[:5])}[/]")
        synopsis = self._synopsis_block(clean_synopsis(a.synopsis))
        if synopsis:
            lines.append("")
            lines.append(synopsis)
        return "\n".join(lines)

    def _synopsis_block(self, text: str) -> str:
        """The synopsis wrapped to the column, cut to what the layout can afford.

        Wrapped here rather than left to the widget so the cut lands on a whole
        word with an ellipsis, and so the number of lines it takes is known.
        """
        if not text:
            return ""
        width = max(20, self._info_width() - 1)
        limit = self._synopsis_lines()
        wrapped = textwrap.wrap(text, width=width)
        if len(wrapped) <= limit:
            return "\n".join(wrapped)
        kept = wrapped[:limit]
        last = kept[-1]
        if len(last) >= width:
            last = last[: width - 1].rsplit(" ", 1)[0]
        kept[-1] = last.rstrip(" ,.;:") + "…"
        return "\n".join(kept)
