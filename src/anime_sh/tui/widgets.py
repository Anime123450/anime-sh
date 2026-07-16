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
    """A ListItem for a single episode number."""

    def __init__(self, number: float, *, watched: bool = False, resume_s: int = 0) -> None:
        self.number = number
        mark = "[green]▸[/green] " if resume_s else ("[dim]✓[/dim] " if watched else "")
        super().__init__(Label(f"{mark}Episode {number:g}"))
