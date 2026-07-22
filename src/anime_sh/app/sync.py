"""SyncService — reconcile local watch progress with a list tracker (AniList).

Two directions, both explicit (the CLI drives them):

* :meth:`push` sends every local progress row to the tracker — a one-time
  catch-up so a freshly-linked account reflects what you have already watched.
* :meth:`pull` imports the tracker's list into the local library (metadata +
  progress), so continue-watching and the episode marks reflect AniList even
  for shows you started elsewhere.

The tracker itself (an AniList adapter) lives in infra; this service only
orchestrates it against the :class:`Library` port, keeping the app layer free of
network detail.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.ports import Library, Tracker


@dataclass(frozen=True, slots=True)
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    skipped: int = 0


class SyncService:
    def __init__(self, library: Library, tracker: Tracker | None) -> None:
        self._library = library
        self._tracker = tracker

    @property
    def enabled(self) -> bool:
        return self._tracker is not None

    async def push(self) -> SyncResult:
        """Push every local progress row to the tracker. Uses cached metadata
        for the planned total so finales are marked COMPLETED."""
        if self._tracker is None:
            return SyncResult()
        pushed = skipped = 0
        for progress in await self._library.all_progress_rows():
            if progress.anime_id.anilist is None or progress.episode <= 0:
                skipped += 1
                continue
            anime = await self._library.get_anime(progress.anime_id)
            total = anime.episode_count if anime else None
            await self._tracker.push(progress, total=total)
            pushed += 1
        return SyncResult(pushed=pushed, skipped=skipped)

    async def pull(self) -> SyncResult:
        """Import the tracker's list into the local library (metadata + progress)."""
        if self._tracker is None:
            return SyncResult()
        pulled = 0
        # AniList adapter offers richer media alongside progress; fall back to
        # bare progress rows for any tracker that only implements the port.
        rows = getattr(self._tracker, "pull_with_media", None)
        if rows is not None:
            for progress, anime in await self._tracker.pull_with_media():
                await self._library.save_anime(anime)
                await self._library.save_progress(progress)
                pulled += 1
        else:
            for progress in await self._tracker.pull():
                await self._library.save_progress(progress)
                pulled += 1
        return SyncResult(pulled=pulled)
