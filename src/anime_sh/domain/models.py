"""Immutable domain models — the stable vocabulary of anime-sh.

Every I/O boundary above the provider layer speaks these types. A provider
that returns a dict instead of one of these is a bug.

Identity spine: every :class:`Anime` is keyed by an :class:`AnimeId` (AniList
id primary). Providers are *sources* attached to a known identity, never the
source of identity itself. That is what makes cross-provider merging a dict
lookup instead of fuzzy title matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Mapping


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Format(str, Enum):
    TV = "TV"
    MOVIE = "MOVIE"
    OVA = "OVA"
    ONA = "ONA"
    SPECIAL = "SPECIAL"
    MUSIC = "MUSIC"
    UNKNOWN = "UNKNOWN"


class Status(str, Enum):
    FINISHED = "FINISHED"
    RELEASING = "RELEASING"
    NOT_YET_RELEASED = "NOT_YET_RELEASED"
    CANCELLED = "CANCELLED"
    HIATUS = "HIATUS"
    UNKNOWN = "UNKNOWN"


class Season(str, Enum):
    WINTER = "WINTER"
    SPRING = "SPRING"
    SUMMER = "SUMMER"
    FALL = "FALL"


class Audio(str, Enum):
    SUB = "SUB"
    DUB = "DUB"


class StreamKind(str, Enum):
    HLS = "HLS"
    MP4 = "MP4"
    DASH = "DASH"


class Quality(str, Enum):
    Q2160 = "2160p"
    Q1080 = "1080p"
    Q720 = "720p"
    Q480 = "480p"
    Q360 = "360p"
    UNKNOWN = "unknown"


class BreakerState(str, Enum):
    """Persisted circuit-breaker state for a provider. The transient
    ``half-open`` probe is derived from OPEN + elapsed cooldown, not stored."""

    CLOSED = "closed"
    OPEN = "open"


# --------------------------------------------------------------------------- #
# Identity + metadata
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AnimeId:
    anilist: int | None
    mal: int | None = None

    def __post_init__(self) -> None:
        if self.anilist is None and self.mal is None:
            raise ValueError("AnimeId needs at least one of anilist/mal")

    @property
    def key(self) -> str:
        """Stable string key for maps and DB rows."""
        if self.anilist is not None:
            return f"anilist:{self.anilist}"
        return f"mal:{self.mal}"


@dataclass(frozen=True, slots=True)
class Title:
    romaji: str | None
    english: str | None = None
    native: str | None = None
    synonyms: tuple[str, ...] = ()

    @property
    def preferred(self) -> str:
        return self.english or self.romaji or self.native or "Unknown"


@dataclass(frozen=True, slots=True)
class Anime:
    id: AnimeId
    title: Title
    format: Format = Format.UNKNOWN
    status: Status = Status.UNKNOWN
    episode_count: int | None = None
    season: Season | None = None
    year: int | None = None
    genres: tuple[str, ...] = ()
    synopsis: str | None = None
    cover_url: str | None = None
    duration_min: int | None = None


# --------------------------------------------------------------------------- #
# Provider-facing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ProviderRef:
    """A resolved mapping: one identity, as seen by one provider."""

    provider: str
    anime_key: str  # provider-native id
    audio: Audio = Audio.SUB
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class Episode:
    anime_id: AnimeId
    number: float  # float handles 13.5 specials
    provider_ref: ProviderRef
    episode_key: str
    title: str | None = None
    aired_at: datetime | None = None
    absolute_number: int | None = None


@dataclass(frozen=True, slots=True)
class StreamCandidate:
    """What a provider hands you: an embed page, not a video."""

    host: str  # "mp4upload"
    url: str
    audio: Audio = Audio.SUB
    headers: Mapping[str, str] = field(default_factory=dict)
    quality_hint: str | None = None


# --------------------------------------------------------------------------- #
# Resolver + player facing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Subtitle:
    url: str
    lang: str
    label: str | None = None
    default: bool = False


@dataclass(frozen=True, slots=True)
class SkipRange:
    start_s: int
    end_s: int


@dataclass(frozen=True, slots=True)
class SkipTimes:
    op: SkipRange | None = None
    ed: SkipRange | None = None


@dataclass(frozen=True, slots=True)
class Stream:
    """What a resolver hands you: something a player can open."""

    url: str
    kind: StreamKind
    quality: Quality = Quality.UNKNOWN
    headers: Mapping[str, str] = field(default_factory=dict)
    subtitles: tuple[Subtitle, ...] = ()
    skip_times: SkipTimes | None = None


# --------------------------------------------------------------------------- #
# User state
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class WatchProgress:
    anime_id: AnimeId
    episode: float
    position_s: int
    duration_s: int
    updated_at: datetime
    completed: bool = False

    @property
    def fraction(self) -> float:
        if self.duration_s <= 0:
            return 0.0
        return min(1.0, self.position_s / self.duration_s)


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Circuit-breaker + health record for one provider, persisted across runs
    so a dead provider stays deprioritised without re-paying its timeout."""

    provider: str
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    opened_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AiringEvent:
    anime: Anime
    episode: float
    airing_at: datetime


@dataclass(frozen=True, slots=True)
class SearchResult:
    anime: Anime
    # Availability is resolved lazily at selection time, never during search.
    availability: str = "UNKNOWN"  # UNKNOWN | AVAILABLE | UNAVAILABLE


# --------------------------------------------------------------------------- #
# Library views — a progress/history/favorite row joined with cached metadata,
# so history and favorites render even when every provider is down.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ResumeItem:
    anime: Anime
    progress: WatchProgress


@dataclass(frozen=True, slots=True)
class HistoryItem:
    anime: Anime
    episode: float
    watched_at: datetime
    provider: str | None
    seconds_watched: int


@dataclass(frozen=True, slots=True)
class FavoriteItem:
    anime: Anime
    added_at: datetime
    note: str | None = None
