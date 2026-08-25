"""Small list-item widgets that carry domain objects alongside their label."""

from __future__ import annotations

from dataclasses import replace

from textual.widgets import Label, ListItem

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
    ) -> None:
        self.anime = anime
        self.resume_episode = resume_episode
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


class EpisodeItem(ListItem):
    """A ListItem for a single episode, styled by state: watched (✓, dim),
    in-progress (▸ + a mini progress bar), up-next (▶, highlighted), plain
    unwatched (○), or not-yet-available (dim, with an air countdown)."""

    def __init__(self, number: float, *, watched: bool = False, resume_s: int = 0,
                 available: bool = True, progress_pct: int | None = None,
                 air_label: str | None = None, is_next: bool = False) -> None:
        self.number = number
        self.available = available
        self.watched = watched
        self.progress_pct = progress_pct
        self.is_next = is_next
        super().__init__(Label(self._label(
            number, watched, resume_s, available, progress_pct, air_label, is_next
        )))

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
