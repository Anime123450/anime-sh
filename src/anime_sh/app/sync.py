"""SyncService — reconcile local watch progress with a list tracker (AniList).

Two directions, both explicit (the CLI drives them):

* :meth:`push` sends your furthest episode per show to the tracker — a one-time
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

from ..domain.models import WatchProgress
from ..domain.ports import Library, Tracker


@dataclass(frozen=True, slots=True)
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    #: Rows there was nothing to send for: no AniList id, or an entry you have
    #: not watched any of. Routine, and not a problem.
    skipped: int = 0
    #: Shows the tracker *rejected* -- a deleted media id, a rate limit that
    #: outlasted its retries. Kept apart from ``skipped`` because a rejection is
    #: the one thing here the user has to know about, and a push is deliberately
    #: non-fatal, so this count is the only signal one ever produces. Both used
    #: to land in ``skipped``, where a real failure was indistinguishable from
    #: the 18 planning entries a synced library carries.
    failed: int = 0


class SyncService:
    def __init__(self, library: Library, tracker: Tracker | None) -> None:
        self._library = library
        self._tracker = tracker

    @property
    def enabled(self) -> bool:
        return self._tracker is not None

    async def push(self) -> SyncResult:
        """Push local progress to the tracker: one call per show, carrying the
        furthest *finished* episode. Uses cached metadata for the planned total
        so finales are marked COMPLETED.

        Finished, not furthest. A tracker entry's number means "this many
        episodes are done", so the episode you are in the middle of is a
        position and not a count -- and sending it claimed an episode you had
        not watched. Worse, `pull` then wrote that claim back as completed with
        no position, so a round trip through the tracker deleted the place you
        had stopped at.

        Per *show*, not per progress row. A tracker entry holds one number, so
        sending every row for a show just set the same entry over and over,
        landing on the highest — the same end state as sending only the highest,
        for as many calls as you have episodes.

        That was not merely wasteful. Each intermediate call set the entry
        *below* where you actually are, so a push that stopped partway — a
        dropped connection, a rate limit that outlasted its retries — left
        finished shows sitting at whatever episode it had reached. Sending one
        call per show makes each show all-or-nothing.
        """
        if self._tracker is None:
            return SyncResult()
        skipped = 0
        furthest: dict[int, WatchProgress] = {}
        for progress in await self._library.all_progress_rows():
            if progress.anime_id.anilist is None or progress.episode <= 0:
                skipped += 1
                continue
            if not progress.completed:
                # AniList's `progress` counts episodes *finished*, so the
                # frontier row -- the one episode you are part-way through -- is
                # the one thing that must not be sent. It was, because this took
                # the furthest row and never asked. On a real library that meant
                # telling AniList "episode 1 watched" for a show 1% in, and "3
                # episodes watched" for one where nothing had been finished at
                # all and episode 3 was half-way through.
                #
                # Not counted as skipped: a show you have started and not
                # finished anything in has nothing to report, which is a correct
                # outcome rather than a rejected row.
                continue
            best = furthest.get(progress.anime_id.anilist)
            # Not relying on the repository's ORDER BY: "the furthest episode"
            # is the property that matters, and it should not quietly become
            # wrong if that query is ever reordered.
            if best is None or progress.episode > best.episode:
                furthest[progress.anime_id.anilist] = progress

        pushed = failed = 0
        for progress in furthest.values():
            anime = await self._library.get_anime(progress.anime_id)
            total = anime.episode_count if anime else None
            try:
                await self._tracker.push(progress, total=total)
            except Exception:
                # One rejected show (a deleted media id, a rate-limit that
                # outlasted its retries) used to abort the whole push and lose
                # everything still queued behind it. Count it and keep going.
                failed += 1
                continue
            pushed += 1
        return SyncResult(pushed=pushed, skipped=skipped, failed=failed)

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
