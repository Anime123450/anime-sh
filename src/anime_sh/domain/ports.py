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
    FavoriteItem,
    HistoryItem,
    ProviderHealth,
    ProviderRef,
    ResumeItem,
    Season,
    SourceOption,
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
        """Map an identity to this provider's native id (the single best entry).
        Cached forever."""

    async def find_sources(self, anime: Anime, audio: Audio) -> list["SourceOption"]:
        """Every entry on this provider that fuzzily matches the show, best
        first — for the source picker. ``match`` is just the top of this list."""

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        """List episodes for a matched show. ``anime_id`` is passed in because
        the provider knows only its own show key, not the identity spine; it
        stamps the supplied id onto each returned Episode."""

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
    """Local, never-expiring user state: progress, history, favorites, plus a
    cache of the AniList metadata for anything the user has touched.

    This is the sacred store — distinct from the disposable cache. Clearing
    the cache must never be able to touch anything behind this port. Cached
    metadata lives here (not in cache.db) so history/favorites/continue-watching
    render offline.
    """

    # progress
    async def get_progress(
        self, anime_id: AnimeId, episode: float
    ) -> WatchProgress | None: ...

    async def save_progress(self, progress: WatchProgress) -> None: ...

    async def continue_watching(self, *, limit: int = 20) -> list["ResumeItem"]: ...

    # metadata cache (identity spine kept warm for the library)
    async def save_anime(self, anime: "Anime") -> None: ...

    async def get_anime(self, anime_id: AnimeId) -> "Anime | None": ...

    # history
    async def add_history(
        self, anime_id: AnimeId, episode: float, *, provider: str | None,
        seconds_watched: int,
    ) -> None: ...

    async def list_history(self, *, limit: int = 50) -> list["HistoryItem"]: ...

    # favorites
    async def add_favorite(self, anime_id: AnimeId, *, note: str | None = None) -> None: ...

    async def remove_favorite(self, anime_id: AnimeId) -> None: ...

    async def is_favorite(self, anime_id: AnimeId) -> bool: ...

    async def list_favorites(self) -> list["FavoriteItem"]: ...


@runtime_checkable
class HealthStore(Protocol):
    """Persists circuit-breaker / health state so it survives restarts."""

    async def get(self, provider: str) -> ProviderHealth | None: ...

    async def save(self, health: ProviderHealth) -> None: ...

    async def all(self) -> list[ProviderHealth]: ...


@runtime_checkable
class StreamProxy(Protocol):
    """Rewrites a resolved stream so a player/downloader can consume it — e.g.
    routing an obfuscated-CDN stream through a local de-obfuscating proxy.
    Returns the stream unchanged when no rewrite is needed."""

    def rewrite(self, stream: "Stream") -> "Stream": ...

    def stop(self) -> None: ...


@runtime_checkable
class Downloader(Protocol):
    """Fetches a resolved stream to a local file (ffmpeg for HLS)."""

    def available(self) -> bool: ...

    async def download(
        self, stream: "Stream", dest: "Path", *,
        on_line: "Callable[[str], None] | None" = None,
    ) -> None: ...


@runtime_checkable
class DownloadStore(Protocol):
    """Records downloads in the sacred store."""

    async def add(self, anime_id: AnimeId, episode: float, path: str) -> int: ...

    async def set_status(
        self, download_id: int, status: "DownloadStatus", *, path: str | None = None
    ) -> None: ...

    async def list(self, *, limit: int = 50) -> list["DownloadItem"]: ...


class PlaybackEvent(Protocol):
    """Emitted by a PlaybackHandle. Concrete kinds live in infra/players."""

    position_s: int
    duration_s: int
    paused: bool
    eof: bool
    reason: str | None  # end-file reason: "eof" vs "quit"/"stop"
