"""In-memory fakes for provider / resolver / player / library.

These let the entire orchestration layer be tested with zero network and in
under a second. If a test here is awkward to write, the ports are wrong — which
is exactly the signal we want on day one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from anime_sh.domain.errors import ProviderError, ResolverError
from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Audio,
    Episode,
    ProviderRef,
    Quality,
    Stream,
    StreamCandidate,
    StreamKind,
    Title,
    WatchProgress,
)


def make_anime(anilist: int = 154587, title: str = "Frieren") -> Anime:
    return Anime(id=AnimeId(anilist=anilist), title=Title(romaji=title))


class FakeProvider:
    """A provider whose behaviour is fully scripted per test."""

    api_version = 1

    def __init__(
        self,
        name: str,
        *,
        priority: int = 0,
        matches: bool = True,
        episodes_for: dict[str, list[float]] | None = None,
        candidate_hosts: list[str] | None = None,
        raise_on: str | None = None,
    ) -> None:
        self.name = name
        self.priority = priority
        self._matches = matches
        self._episodes_for = episodes_for
        self._candidate_hosts = candidate_hosts or ["mp4upload"]
        self._raise_on = raise_on
        self.calls: list[str] = []

    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        self.calls.append("match")
        if self._raise_on == "match":
            raise ProviderError(f"{self.name} match boom")
        if not self._matches:
            return None
        return ProviderRef(provider=self.name, anime_key=f"{self.name}-key", audio=audio)

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        self.calls.append("episodes")
        if self._raise_on == "episodes":
            raise ProviderError(f"{self.name} episodes boom")
        numbers = (self._episodes_for or {}).get(ref.anime_key, [1.0, 2.0, 18.0])
        return [
            Episode(
                anime_id=anime_id,
                number=n,
                provider_ref=ref,
                episode_key=f"{self.name}-ep{n:g}",
            )
            for n in numbers
        ]

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        self.calls.append("candidates")
        if self._raise_on == "candidates":
            raise ProviderError(f"{self.name} candidates boom")
        return [
            StreamCandidate(host=host, url=f"https://{host}/embed/{episode.episode_key}")
            for host in self._candidate_hosts
        ]


class FakeResolver:
    """Handles exactly one host; can be told to fail or yield streams."""

    api_version = 1

    def __init__(
        self,
        name: str,
        *,
        host: str,
        behaviour: str = "ok",  # ok | fail | empty | crash
        quality: Quality = Quality.Q1080,
    ) -> None:
        self.name = name
        self._host = host
        self._behaviour = behaviour
        self._quality = quality
        self.calls = 0

    def handles(self, candidate: StreamCandidate) -> bool:
        return candidate.host == self._host

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        self.calls += 1
        if self._behaviour == "fail":
            raise ResolverError(f"{self.name} cannot resolve {candidate.host}")
        if self._behaviour == "crash":
            raise RuntimeError("unexpected explosion")
        if self._behaviour == "empty":
            return []
        return [
            Stream(
                url=f"https://cdn/{candidate.host}/video.m3u8",
                kind=StreamKind.HLS,
                quality=self._quality,
            )
        ]


class FakeLibrary:
    def __init__(self, progress: WatchProgress | None = None) -> None:
        self._progress = progress
        self.saved: list[WatchProgress] = []
        self.saved_anime: list = []
        self.history: list[tuple] = []
        self.favorites: dict[int, str | None] = {}

    async def get_progress(self, anime_id: AnimeId, episode: float):
        if self._progress and self._progress.episode == episode:
            return self._progress
        return None

    async def save_progress(self, progress: WatchProgress) -> None:
        self.saved.append(progress)

    async def all_progress(self, anime_id: AnimeId) -> list[WatchProgress]:
        return [p for p in ([self._progress] if self._progress else []) + self.saved]

    async def all_progress_rows(self) -> list[WatchProgress]:
        return [p for p in ([self._progress] if self._progress else []) + self.saved]

    async def continue_watching(self, *, limit: int = 20):
        return []

    async def save_anime(self, anime) -> None:
        self.saved_anime.append(anime)

    async def get_anime(self, anime_id: AnimeId):
        return next((a for a in self.saved_anime if a.id == anime_id), None)

    async def add_history(self, anime_id, episode, *, provider, seconds_watched) -> None:
        self.history.append((anime_id, episode, provider, seconds_watched))

    async def list_history(self, *, limit: int = 50):
        return []

    async def add_favorite(self, anime_id, *, note=None) -> None:
        self.favorites[anime_id.anilist] = note

    async def remove_favorite(self, anime_id) -> None:
        self.favorites.pop(anime_id.anilist, None)

    async def is_favorite(self, anime_id) -> bool:
        return anime_id.anilist in self.favorites

    async def list_favorites(self):
        return []


def resume_at(seconds: int, episode: float = 18.0) -> WatchProgress:
    return WatchProgress(
        anime_id=AnimeId(anilist=154587),
        episode=episode,
        position_s=seconds,
        duration_s=1400,
        updated_at=datetime.now(timezone.utc),
    )
