"""SearchService — thin orchestration over the metadata source.

Search touches only the metadata source (AniList), never a scraper: it is
instant and reliable, and provider availability is resolved lazily later, at
play time. That is what keeps search-as-you-type fast in the TUI.
"""

from __future__ import annotations

from ..domain.models import Anime, AnimeId, SearchResult
from ..domain.ports import MetadataSource


class SearchService:
    def __init__(self, metadata: MetadataSource) -> None:
        self._metadata = metadata

    async def search(self, query: str, *, limit: int = 20) -> list[SearchResult]:
        animes = await self._metadata.search(query, limit=limit)
        return [SearchResult(anime=a) for a in animes]

    async def best_match(self, query: str) -> Anime | None:
        results = await self._metadata.search(query, limit=5)
        return results[0] if results else None

    async def get(self, anime_id: AnimeId) -> Anime:
        return await self._metadata.get(anime_id)
