"""Small list-item widgets that carry domain objects alongside their label."""

from __future__ import annotations

from textual.widgets import Label, ListItem

from ..domain.models import Anime
from .format import progress_bar


def _lit(text: str) -> str:
    """Escape a string so Textual renders it literally inside a markup label.

    Titles come from AniList/providers and routinely contain square brackets —
    a "[Mini]" batch, "[Oshi no Ko]" — which Textual's markup parser would
    otherwise eat as a style tag, making the text vanish. Escaping the opening
    bracket (after any backslash) keeps it visible.
    """
    return text.replace("\\", "\\\\").replace("[", r"\[")


class AnimeItem(ListItem):
    """A ListItem that remembers which Anime (and optional resume episode) it is."""

    def __init__(
        self,
        anime: Anime,
        *,
        subtitle: str = "",
        resume_episode: float | None = None,
        dim: bool = False,
        progress: float | None = None,
    ) -> None:
        self.anime = anime
        self.resume_episode = resume_episode
        self._dim = dim
        self._progress = progress
        self._label = Label(self._compose_label(subtitle))
        super().__init__(self._label)

    def _compose_label(self, subtitle: str) -> str:
        label = _lit(self.anime.title.preferred)
        if subtitle:
            label = f"{label}  [dim]{_lit(subtitle)}[/dim]"
        # A small bar mirrors the detail screen for a show you're partway through.
        if self._progress is not None and not self._dim:
            label = f"{label}  {progress_bar(self._progress, 5, color='cyan')}"
        # A whole-row dim marks a show you're caught up on (waiting for the next
        # episode) — greyed out so the titles you *can* watch stand out.
        if self._dim:
            label = f"[dim]{label}[/dim]"
        return label

    def set_subtitle(self, subtitle: str) -> None:
        """Update the row's subtitle in place — used to tick airing countdowns
        without rebuilding (and disrupting selection in) the list."""
        self._label.update(self._compose_label(subtitle))


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
            # An unreleased episode: show how long to wait, not a flat "n/a".
            tail = air_label or "not aired yet"
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
