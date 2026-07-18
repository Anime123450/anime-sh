"""Small list-item widgets that carry domain objects alongside their label."""

from __future__ import annotations

from textual.widgets import Label, ListItem

from ..domain.models import Anime


class AnimeItem(ListItem):
    """A ListItem that remembers which Anime (and optional resume episode) it is."""

    def __init__(self, anime: Anime, *, subtitle: str = "", resume_episode: float | None = None) -> None:
        self.anime = anime
        self.resume_episode = resume_episode
        label = anime.title.preferred
        if subtitle:
            label = f"{label}  [dim]{subtitle}[/dim]"
        super().__init__(Label(label))


class EpisodeItem(ListItem):
    """A ListItem for a single episode number. Unavailable episodes (not yet
    aired / provider lacks them) stay listed but dimmed, so the full season is
    always visible; watched ones get a ✓ and in-progress ones a ▸ with the
    percentage watched."""

    def __init__(self, number: float, *, watched: bool = False, resume_s: int = 0,
                 available: bool = True, progress_pct: int | None = None) -> None:
        self.number = number
        self.available = available
        self.watched = watched
        self.progress_pct = progress_pct
        if not available:
            super().__init__(Label(f"[dim]Episode {number:g}  · not available yet[/dim]"))
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
            f"{source.title}  "
            f"[cyan]{source.provider}[/cyan] [dim]· {eps} · {source.audio.value.lower()}[/dim]"
        )
        super().__init__(Label(label))
