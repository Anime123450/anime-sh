"""LibraryService — the user's local shelf: resume, history, favorites.

Thin orchestration over the :class:`Library` port. Adding a favorite also caches
the show's metadata so the favorites list renders offline; the identity spine
(AniList id) is what everything keys on.
"""

from __future__ import annotations

from ..domain.models import (
    Anime,
    AnimeId,
    FavoriteItem,
    HistoryItem,
    ResumeItem,
)
from ..domain.ports import Library


class LibraryService:
    def __init__(self, library: Library) -> None:
        self._library = library

    async def continue_watching(self, *, limit: int = 20) -> list[ResumeItem]:
        return await self._library.continue_watching(limit=limit)

    async def history(self, *, limit: int = 50) -> list[HistoryItem]:
        return await self._library.list_history(limit=limit)

    async def favorites(self) -> list[FavoriteItem]:
        return await self._library.list_favorites()

    async def add_favorite(self, anime: Anime, *, note: str | None = None) -> None:
        await self._library.save_anime(anime)
        await self._library.add_favorite(anime.id, note=note)

    async def remove_favorite(self, anime_id: AnimeId) -> None:
        await self._library.remove_favorite(anime_id)

    async def is_favorite(self, anime_id: AnimeId) -> bool:
        return await self._library.is_favorite(anime_id)
