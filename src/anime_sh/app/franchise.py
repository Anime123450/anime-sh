"""A show's whole franchise: every season, movie, OVA and special, in watch
order, each with the user's own progress.

Nothing here assumes you start at season one. You may have watched season 1
somewhere else and want the movie that sits between seasons 2 and 3. So the
franchise is a map you can enter anywhere. It shows where you actually are
(from your progress, wherever it came from) and suggests the next thing from
there, not from the beginning.

CONTRACT: the TUI codes against the names and fields in this module. Keep
them stable; add, don't rename.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..domain.models import Anime


class EntryStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class FranchiseEntry:
    anime: Anime
    #: How this entry relates to the franchise, for display, in lowercase:
    #: "season", "movie", "ova", "special", "side story", "spin-off",
    #: "summary", "alternative", ...
    kind: str
    #: True for the main story line: the TV/ONA seasons and movies on the
    #: prequel/sequel chain. Side stories, recaps and spin-offs are False.
    main_line: bool
    status: EntryStatus
    watched: int          # completed episodes recorded for this entry
    total: int | None     # episode count, None while unknown/airing
    is_current: bool      # the entry whose detail page asked for this


@dataclass(frozen=True, slots=True)
class Franchise:
    #: Watch order: by start date (release order), main line and side stories
    #: interleaved where they actually came out. Recaps last within their year.
    entries: tuple[FranchiseEntry, ...] = ()
    current_index: int = 0
    #: The entry to watch next, judged from the user's progress across the
    #: whole franchise rather than from season one. None when everything is
    #: done (or nothing is known).
    suggested_index: int | None = None
    #: True when the relation walk hit its cap, so the list may be partial.
    truncated: bool = False

    @property
    def current(self) -> FranchiseEntry | None:
        return self.entries[self.current_index] if self.entries else None

    @property
    def suggested(self) -> FranchiseEntry | None:
        if self.suggested_index is None:
            return None
        return self.entries[self.suggested_index]


@dataclass(frozen=True, slots=True)
class EpisodeInfo:
    """Per-episode metadata for display. Every field is optional: many shows
    have none of it, and the detail screen must look right without it."""
    number: int
    title: str | None = None
    aired: str | None = None      # ISO date, "2016-04-04"
    filler: bool = False
    recap: bool = False


@dataclass
class FranchiseService:
    """Builds `Franchise` maps and records watched-elsewhere progress.

    Implemented in the franchise work package; the TUI reaches it as
    ``app.services.franchise`` (None-safe: older wiring may lack it).
    """
    metadata: object
    library: object
    _cache: dict = field(default_factory=dict, repr=False)

    async def build(self, anime: Anime) -> Franchise:  # pragma: no cover - stub
        raise NotImplementedError

    async def mark_entry_watched(self, anime: Anime) -> int:  # pragma: no cover
        """Mark every episode of one entry watched (seen elsewhere).
        Returns how many episodes were recorded."""
        raise NotImplementedError

    async def episode_info(self, anime: Anime) -> dict[int, EpisodeInfo]:  # pragma: no cover
        """Episode titles/air dates/filler flags, best effort; {} on failure."""
        raise NotImplementedError
