"""Ports — the contracts every adapter implements.

These are structural (:class:`typing.Protocol`) so an adapter satisfies a port
just by having the right shape; it never needs to import from here at runtime.
All boundary methods are async because every real implementation does I/O.
"""

from __future__ import annotations

from datetime import date
from typing import AsyncIterator, Protocol, runtime_checkable

from .models import (
    AiringEvent,
    Anime,
    AnimeId,
    Audio,
    Episode,
    ProviderRef,
    Season,
    Stream,
    StreamCandidate,
    WatchProgress,
)

# The interface version an adapter is built against. The registry refuses to
# load a plugin whose declared api_version does not match. Bump on breaks.
API_VERSION = 1


@runtime_checkable
class MetadataSource(Protocol):
    """Identity + catalog. AniList is the reference implementation."""

    name: str

    async def search(self, query: str, *, limit: int = 20) -> list[Anime]: ...

    async def get(self, id: AnimeId) -> Anime: ...

    async def trending(self, *, limit: int = 30) -> list[Anime]: ...

    async def seasonal(self, season: Season, year: int) -> list[Anime]: ...

    async def airing_schedule(
        self, start: date, end: date
    ) -> list[AiringEvent]: ...


@runtime_checkable
class ProviderCapabilities(Protocol):
    dub: bool
    downloads: bool
    subtitles: bool


@runtime_checkable
class Provider(Protocol):
    """A streaming source. Knows how to find episodes, not video URLs."""

    name: str
    priority: int
    api_version: int

    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        """Map an identity to this provider's native id. Cached forever."""

    async def episodes(self, ref: ProviderRef) -> list[Episode]: ...

    async def candidates(self, episode: Episode) -> list[StreamCandidate]: ...


@runtime_checkable
class Resolver(Protocol):
    """Turns an embed candidate into playable streams. Knows hosts, not anime."""

    name: str
    api_version: int

    def handles(self, candidate: StreamCandidate) -> bool: ...

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]: ...


@runtime_checkable
class PlaybackHandle(Protocol):
    """A live playback session. The seam that keeps resume / skip / presence
    ignorant of which player is running."""

    def events(self) -> AsyncIterator["PlaybackEvent"]: ...

    async def seek(self, seconds: int) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class Player(Protocol):
    name: str

    def available(self) -> bool: ...

    async def play(
        self, stream: Stream, *, title: str, start_s: int = 0
    ) -> PlaybackHandle: ...


@runtime_checkable
class Tracker(Protocol):
    """AniList / MAL / local-only progress sync."""

    name: str

    async def push(self, progress: WatchProgress) -> None: ...

    async def pull(self) -> list[WatchProgress]: ...


@runtime_checkable
class Library(Protocol):
    """Local, never-expiring user state: progress, history, favorites.

    This is the sacred store — distinct from the disposable cache. Clearing
    the cache must never be able to touch anything behind this port.
    """

    async def get_progress(
        self, anime_id: AnimeId, episode: float
    ) -> WatchProgress | None: ...

    async def save_progress(self, progress: WatchProgress) -> None: ...

    async def continue_watching(self, *, limit: int = 20) -> list[WatchProgress]: ...


class PlaybackEvent(Protocol):
    """Emitted by a PlaybackHandle. Concrete kinds live in infra/players."""

    position_s: int
    duration_s: int
    paused: bool
    eof: bool
