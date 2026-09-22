"""`stats` and `wrapped` have to agree about the same library.

On a real library they read:

    anime stats    137 episodes finished across 76 shows
    anime wrapped   46 episodes · 15 shows

Both said "episodes" and neither said what it counted, which reads as one of
them being broken. Two separate defects were hiding in that:

* `stats` paired "episodes finished" with a show count that included shows
  where nothing was ever finished. Those 137 episodes spanned 59 shows; the
  other 17 were started and abandoned.
* `wrapped` counts playback sessions, because hours and streaks cannot be
  computed from anything else - but it never said so.
"""

from __future__ import annotations

from datetime import datetime, timezone

from anime_sh.domain.models import AnimeId, WatchStats, WatchProgress
from anime_sh.domain.wrapped import summarise

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _progress(anilist: int, episode: float, *, completed: bool) -> WatchProgress:
    return WatchProgress(
        anime_id=AnimeId(anilist=anilist), episode=episode, position_s=600,
        duration_s=1400, updated_at=NOW, completed=completed,
    )


# -- the WatchStats shape --------------------------------------------------- #
def test_watchstats_can_be_built_positionally():
    """A dataclass field with a default in front of one without is a TypeError
    at import time, and takes the whole application down with it - which is
    exactly what adding `shows_completed` in the wrong place did."""
    # A bad ordering raises at class-creation time, so the import at the top of
    # this file is half the guard; this pins the positional contract too.
    stats = WatchStats(1, 2, 3, 4)
    assert (stats.episodes_completed, stats.shows, stats.sessions) == (1, 2, 3)
    assert stats.shows_completed == 0, "must stay optional for existing callers"


# -- the counts ------------------------------------------------------------- #
def _library():
    """Two shows finished, one merely started."""
    return [
        _progress(1, 1.0, completed=True),
        _progress(1, 2.0, completed=True),
        _progress(2, 1.0, completed=True),
        _progress(3, 1.0, completed=False),  # started, never finished
    ]


class _Library:
    """Just the two reads `stats` performs."""

    def __init__(self, progress):
        self._progress = progress

    async def list_history(self, *, limit=50):
        return []

    async def all_progress_rows(self):
        return list(self._progress)


def test_episodes_finished_and_shows_finished_describe_the_same_rows():
    """The real service, not arithmetic on a fixture: "N episodes finished
    across M shows" has to be a true sentence about the same rows."""
    import asyncio

    from anime_sh.app.library import LibraryService

    stats = asyncio.run(LibraryService(_Library(_library())).stats())
    assert stats.episodes_completed == 3
    assert stats.shows_completed == 2, "shows in which something was finished"
    assert stats.shows == 3, "shows touched at all, including the abandoned one"
    assert stats.shows_completed < stats.shows, "fixture must exercise the gap"


def test_wrapped_carries_the_wider_marked_total():
    """So a reader can see why its headline is smaller than `stats`, instead of
    concluding that one of them is wrong."""
    w = summarise([], progress=_library())
    assert w.marked_episodes == 3
    assert w.marked_shows == 2, "shows where something was actually finished"
    assert w.empty, "no playback sessions at all"


def test_marked_totals_are_zero_without_progress():
    w = summarise([])
    assert (w.marked_episodes, w.marked_shows) == (0, 0)
    assert w.has_wider_total is False


def test_has_wider_total_only_when_marked_exceeds_played():
    played_more = summarise([], progress=[])
    assert played_more.has_wider_total is False
    wider = summarise([], progress=_library())
    assert wider.has_wider_total is True, "3 marked vs 0 played"
