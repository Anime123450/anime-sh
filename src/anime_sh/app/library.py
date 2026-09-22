"""LibraryService — the user's local shelf: resume, history, favorites.

Thin orchestration over the :class:`Library` port. Adding a favorite also caches
the show's metadata so the favorites list renders offline; the identity spine
(AniList id) is what everything keys on.
"""

from __future__ import annotations

from collections import Counter

from ..domain.errors import AnimeShError
from ..domain.models import (
    Anime,
    AnimeId,
    FavoriteItem,
    HistoryItem,
    ResumeItem,
    WatchProgress,
    WatchStats,
)
from ..domain.ports import Library


class LibraryService:
    def __init__(self, library: Library) -> None:
        self._library = library

    async def continue_watching(self, *, limit: int = 20) -> list[ResumeItem]:
        return await self._library.continue_watching(limit=limit)

    async def progress_for(self, anime_id: AnimeId) -> list[WatchProgress]:
        """Every episode's watch progress for one show — feeds the detail
        screen's watched/in-progress marks."""
        return await self._library.all_progress(anime_id)

    async def unmark(self, anime_id: AnimeId) -> int:
        """Clear all local watch progress for a show. Returns rows removed."""
        return await self._library.delete_progress(anime_id)

    #: Ceiling for a catch-up on a show whose episode count we do not know —
    #: an ongoing or unannounced season. The longest thing anyone will plausibly
    #: mark is a shounen in the low thousands (One Piece is ~1140), and marking
    #: that costs about four seconds. Anything above this is a typo, and taking
    #: a typo literally is what made `-e 99999` sit there for six minutes.
    MAX_CATCH_UP = 2000

    #: How far past a *known* episode count a single mark may still be real.
    #: AniList's count lags an airing season and specials get numbered past the
    #: finale, so an exact ceiling would refuse legitimate marks; this catches
    #: the typo without arguing about the edge cases.
    SINGLE_MARK_HEADROOM = 100

    def _check_mark_bounds(self, anime: Anime, up_to: float, *, single: bool) -> None:
        """Refuse a mark that cannot mean what it says.

        Episode 0 is real — some shows number a prologue that way — so a single
        mark may be 0. A *catch-up* to 0 or less cannot be: it names an empty
        range, which silently marked nothing while reporting success.
        """
        floor = 0.0 if single else 1.0
        if up_to < floor:
            raise AnimeShError(
                f"episode must be {floor:g} or higher (got {up_to:g})"
            )
        total = anime.episode_count
        if single:
            # A single mark used to skip every ceiling, which made
            # `mark "Frieren" -e 99999 --single` legal on a 28-episode show. That
            # is not merely an odd local row: `sync push` sends the *furthest*
            # episode per show, so the next push would set the tracker to 99999
            # — the same shape of damage this project has already done once.
            #
            # The ceiling is generous rather than exact, because a single mark
            # legitimately can exceed a known total: AniList's count lags an
            # airing season, and specials are sometimes numbered past the
            # finale. Orders of magnitude beyond it is a typo.
            ceiling = (
                total + self.SINGLE_MARK_HEADROOM
                if total is not None
                else self.MAX_CATCH_UP
            )
            if up_to > ceiling:
                where = (
                    f"{anime.title.preferred} has {total} episodes"
                    if total is not None
                    else f"the limit is {self.MAX_CATCH_UP} when the count is unknown"
                )
                raise AnimeShError(
                    f"refusing to mark episode {up_to:g}: {where}. "
                    "A tracker push sends your furthest episode, so a typo here "
                    "would follow you to AniList."
                )
            return
        if total is not None and up_to > total:
            raise AnimeShError(
                f"{anime.title.preferred} has {total} episodes "
                f"(asked to mark up to {up_to:g})"
            )
        if total is None and up_to > self.MAX_CATCH_UP:
            raise AnimeShError(
                f"refusing to mark {up_to:g} episodes at once; "
                f"the limit is {self.MAX_CATCH_UP} when the episode count is "
                f"unknown"
            )

    async def mark_watched(
        self, anime: Anime, up_to: float, *, single: bool = False
    ) -> list[float]:
        """Mark episodes complete without playing. By default every episode
        1..``up_to`` (catch-up); ``single`` marks only ``up_to``. Caches the
        show's metadata so it renders offline. Returns the episodes marked.

        Bounds are checked here rather than in the CLI so that every caller
        gets them, and so the number the caller goes on to report is one that
        was actually written.
        """
        from datetime import datetime, timezone

        self._check_mark_bounds(anime, up_to, single=single)
        numbers = [up_to] if single else [float(n) for n in range(1, int(up_to) + 1)]
        now = datetime.now(timezone.utc)
        await self._library.save_anime(anime)
        for n in numbers:
            await self._library.save_progress(
                WatchProgress(anime_id=anime.id, episode=n, position_s=1,
                              duration_s=1, updated_at=now, completed=True)
            )
        return numbers

    async def history(self, *, limit: int = 50) -> list[HistoryItem]:
        return await self._library.list_history(limit=limit)

    async def wrapped(self, *, year: int | None = None):
        """The year in review. Pure computation lives in the domain; this only
        fetches the rows it needs."""
        from ..domain.wrapped import summarise

        history = await self._library.list_history(limit=1_000_000)
        # Progress too, for the "marked watched" total. `stats` counts those and
        # `wrapped` counted only playback, so the same library reported two very
        # different episode counts with no hint why.
        progress = await self._library.all_progress_rows()
        return summarise(history, year=year, progress=progress)

    async def stats(self) -> WatchStats:
        """Summarize watch history: episodes, hours, top providers and genres.

        Time and provider/genre breakdowns come from history (weighted by how
        much you actually watched); episode/show counts come from progress."""
        history = await self._library.list_history(limit=1_000_000)
        progress = await self._library.all_progress_rows()
        providers: Counter[str] = Counter()
        genres: Counter[str] = Counter()
        total_seconds = 0
        for h in history:
            total_seconds += max(h.seconds_watched, 0)
            if h.provider:
                providers[h.provider] += 1
            for g in h.anime.genres:
                genres[g] += 1
        return WatchStats(
            episodes_completed=sum(1 for p in progress if p.completed),
            shows=len({p.anime_id.anilist for p in progress if p.anime_id.anilist}),
            shows_completed=len(
                {p.anime_id.anilist for p in progress
                 if p.completed and p.anime_id.anilist}
            ),
            sessions=len(history),
            total_seconds=total_seconds,
            top_providers=tuple(providers.most_common(5)),
            top_genres=tuple(genres.most_common(8)),
        )

    async def favorites(self) -> list[FavoriteItem]:
        return await self._library.list_favorites()

    async def add_favorite(self, anime: Anime, *, note: str | None = None) -> None:
        await self._library.save_anime(anime)
        await self._library.add_favorite(anime.id, note=note)

    async def remove_favorite(self, anime_id: AnimeId) -> None:
        await self._library.remove_favorite(anime_id)

    async def is_favorite(self, anime_id: AnimeId) -> bool:
        return await self._library.is_favorite(anime_id)
