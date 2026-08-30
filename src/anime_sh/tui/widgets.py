"""Small list-item widgets that carry domain objects alongside their label."""

from __future__ import annotations

from dataclasses import replace

from textual.widgets import Label, ListItem, ListView

from ..domain.models import Anime
from .format import progress_bar
from .rows import Columns, Row, _lit, render


class AnimeItem(ListItem):
    """A grid row that remembers which Anime (and optional resume episode) it is.

    The row is stored as semantic cells rather than a finished string so it can
    be re-laid-out when the terminal is resized, and its countdown ticked in
    place, without rebuilding the list — rebuilding would throw away the user's
    selection every minute.
    """

    def __init__(
        self,
        anime: Anime,
        row: Row,
        cols: Columns,
        *,
        resume_episode: float | None = None,
        fraction: float = 0.0,
    ) -> None:
        self.anime = anime
        self.resume_episode = resume_episode
        # How far into `resume_episode` you are, 0–1. Carried on the row so the
        # context rail can draw it for whichever row the cursor lands on: the
        # rendered Row keeps a *bar*, which cannot be measured back into a
        # number, and re-reading progress from the database on every keypress
        # would put a query behind the arrow keys.
        self.fraction = fraction
        self._row = row
        self._cols = cols
        self._label = Label(render(row, cols))
        super().__init__(self._label)

    def relayout(self, cols: Columns) -> None:
        """Re-render at new column widths after a terminal resize."""
        self._cols = cols
        self._label.update(render(self._row, cols))

    def set_status(self, status: str, status_cells: int | None = None) -> None:
        """Update only the status cell — used to tick airing countdowns without
        rebuilding (and so disrupting selection in) the list."""
        self._row = replace(self._row, status=status, status_cells=status_cells)
        self._label.update(render(self._row, self._cols))


class EpisodeGrid(ListView):
    """A ListView laid out as a grid, whose cursor moves like one.

    A ListView cursor is linear, so Down goes to the next *item* — which, once
    the items are tiled into columns, reads as moving one cell to the right.
    Overriding the cursor actions here rather than binding Up/Down on the screen
    is what actually works: the focused widget's own bindings are consulted
    before the screen's, so a screen-level Down never fires while the list has
    focus. (Verified the hard way — the screen binding moved the cursor by one.)

    `columns` is set by whoever sizes the grid; 1 keeps this behaving exactly
    like an ordinary list until then.
    """

    columns = 1

    def _step(self, delta: int) -> None:
        count = len(self.children)
        if not count or self.index is None:
            return
        # Clamped, not wrapped: in a grid, wrapping throws the cursor corner to
        # corner instead of moving it a step.
        self.index = max(0, min(count - 1, self.index + delta))

    def action_cursor_down(self) -> None:
        self._step(self.columns)

    def action_cursor_up(self) -> None:
        self._step(-self.columns)


class EpisodeItem(ListItem):
    """A ListItem for a single episode, styled by state: watched (✓, dim),
    in-progress (▸ + a mini progress bar), up-next (▶, highlighted), plain
    unwatched (○), or not-yet-available (dim, with an air countdown)."""

    def __init__(self, number: float, *, watched: bool = False, resume_s: int = 0,
                 available: bool = True, progress_pct: int | None = None,
                 air_label: str | None = None, is_next: bool = False,
                 width: int = 4, downloaded: bool = False) -> None:
        self.number = number
        self.available = available
        self.watched = watched
        self.progress_pct = progress_pct
        self.is_next = is_next
        self.downloaded = downloaded
        # The full sentence — "Episode 5 · not on this source", the progress bar,
        # the air countdown — is kept, but it no longer lives in the cell. A
        # variable-length label cannot tile into a grid, and a one-per-line list
        # of 1175 ONE PIECE episodes is not something anyone can use. The cell
        # carries state and number; the screen shows this line for whichever
        # episode the cursor is on.
        self.detail = self._label(
            number, watched, resume_s, available, progress_pct, air_label, is_next
        )
        if downloaded:
            self.detail += "  [dim]· on disk[/dim]"
        super().__init__(Label(self._cell(
            number, watched, resume_s, available, progress_pct, is_next, width,
            downloaded,
        )))

    @staticmethod
    def _cell(number, watched, resume_s, available, progress_pct, is_next, width,
              downloaded=False):
        """One uniform grid cell: a state glyph, the episode number, and a slot
        saying whether it is on disk.

        Right-aligned to a common width so the columns line up whatever the
        series length — 9 and 1175 sit in the same grid without ragging it.

        "On disk" is a second, independent fact about an episode: it can be
        watched *and* downloaded, or unwatched and downloaded. It cannot share
        the glyph, which already carries watch state, so it gets a trailing slot
        of its own — a space when absent, so every cell stays the same width and
        the grid keeps its columns.
        """
        n = f"{number:g}".rjust(width)
        disk = "[dim]⤓[/dim]" if downloaded else " "
        if not available:
            return f"[grey42]○ {n}[/grey42]{disk}"
        if watched:
            return f"[green]✓[/green] [dim]{n}[/dim]{disk}"
        if progress_pct or resume_s:
            return f"[green]▸[/green] [b]{n}[/b]{disk}"
        if is_next:
            return f"[cyan]▶ {n}[/cyan]{disk}"
        return f"[grey54]○[/grey54] {n}{disk}"

    @staticmethod
    def _label(number, watched, resume_s, available, progress_pct, air_label, is_next):
        n = f"{number:g}"
        if not available:
            # Why it can't be played. "Hasn't aired" and "this source doesn't
            # carry it" are different things, and calling both "not aired yet"
            # made a season that finished airing in 2025 claim its later episodes
            # were unreleased — when really the chosen source only had four.
            tail = air_label or "not on this source"
            return f"[grey42]○  Episode {n}[/grey42]  [dim]· {tail}[/dim]"
        if watched:
            return f"[green]✓[/green]  [dim]Episode {n}[/dim]"
        if progress_pct or resume_s:
            pct = progress_pct or 0
            bar = progress_bar(pct / 100, 10, color="green")
            return f"[green]▸[/green]  [b]Episode {n}[/b]   {bar}  [green]{pct}%[/green]"
        if is_next:
            return f"[cyan]▶  Episode {n}[/cyan]  [dim]· up next[/dim]"
        return f"[grey54]○[/grey54]  Episode {n}"


class SourceItem(ListItem):
    """A ListItem for one provider entry in the source picker."""

    def __init__(self, source) -> None:
        self.source = source
        eps = f"{source.episode_count} eps" if source.episode_count else "? eps"
        label = (
            f"{_lit(source.title)}  "
            f"[cyan]{_lit(source.provider)}[/cyan] [dim]· {eps} · {source.audio.value.lower()}[/dim]"
        )
        super().__init__(Label(label))
