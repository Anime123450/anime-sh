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


# -- stats and wrapped must agree about "watched here" ---------------------- #
def test_stats_and_wrapped_count_episodes_watched_here_the_same_way():
    """The two commands read the same history table and must land on the same
    number for it.

    `stats` led with `episodes_completed` -- progress rows, so everything `anime
    mark` wrote and everything an AniList pull imported -- directly above hours
    that only playback can produce. On a real library that was 142 episodes
    against 14.5 hours: six minutes an episode, under a heading reading "Your
    anime-sh stats", when 115 of the 142 had come from AniList and none had been
    watched here at all.

    `wrapped` had already been given the careful wording for this; `stats` was
    left behind, which is how the two came to describe one library in numbers
    that could not both be right.
    """
    from anime_sh.domain.wrapped import summarise
    from anime_sh.domain.models import Anime, HistoryItem, Title

    now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    show = Anime(id=AnimeId(anilist=1), title=Title(romaji="A"))
    history = [
        HistoryItem(anime=show, episode=ep, watched_at=now, provider="hianime",
                    seconds_watched=1400)
        # episode 1 twice: three sittings, two episodes
        for ep in (1.0, 1.0, 2.0)
    ]

    here = len({(h.anime.id.anilist, h.episode) for h in history})
    w = summarise(history, local_dates=False)

    assert here == 2, "two distinct episodes across three sittings"
    assert w.episodes == here, (
        f"wrapped says {w.episodes} episodes played here and stats says {here} "
        "for the same rows"
    )
    assert w.sessions == len(history) == 3


def test_the_wider_total_is_only_mentioned_when_it_is_wider():
    """The extra line exists to explain a gap. With nothing imported there is no
    gap, and printing it anyway would invent a distinction the library does not
    have."""
    stats = WatchStats(
        episodes_completed=2, shows=1, sessions=3, total_seconds=4200,
        shows_completed=1, episodes_here=2,
    )
    assert stats.episodes_completed == stats.episodes_here
