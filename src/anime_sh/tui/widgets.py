"""Small list-item widgets that carry domain objects alongside their label."""

from __future__ import annotations

from textual.widgets import Label, ListItem

from ..domain.models import Anime


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
    ) -> None:
        self.anime = anime
        self.resume_episode = resume_episode
        self._dim = dim
        self._label = Label(self._compose_label(subtitle))
        super().__init__(self._label)

    def _compose_label(self, subtitle: str) -> str:
        label = _lit(self.anime.title.preferred)
        if subtitle:
            label = f"{label}  [dim]{_lit(subtitle)}[/dim]"
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
    """A ListItem for a single episode number. Unavailable episodes (not yet
    aired / provider lacks them) stay listed but dimmed, so the full season is
    always visible; watched ones get a ✓ and in-progress ones a ▸ with the
    percentage watched."""

    def __init__(self, number: float, *, watched: bool = False, resume_s: int = 0,
                 available: bool = True, progress_pct: int | None = None,
                 air_label: str | None = None) -> None:
        self.number = number
        self.available = available
        self.watched = watched
        self.progress_pct = progress_pct
        if not available:
            # Show a countdown when we know when it airs ("airs in 4d 3h"), so an
            # unreleased episode tells you how long to wait instead of a flat
            # "not available yet".
            tail = air_label or "not available yet"
            super().__init__(Label(f"[dim]Episode {number:g}  · {tail}[/dim]"))
            return
        if watched:
            label = f"[green]✓[/green] [dim]Episode {number:g}[/dim]"
        elif progress_pct or resume_s:
            pct = f"  [dim]· {progress_pct}%[/dim]" if progress_pct else ""
            label = f"[green]▸[/green] Episode {number:g}{pct}"
        else:
            label = f"Episode {number:g}"
        super().__init__(Label(label))


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
