"""A cached airing schedule must not outlive the season it describes.

``save_anime`` deliberately never clears ``next_airing_episode`` /
``next_airing_at``, so that a caller who happens not to know a show's schedule
cannot wipe the one a metadata fetch stored. The cost is that a show which has
*finished* keeps the last "next episode" it ever had, forever. Readers trusted
it, which is how an episode released months ago came back as "hasn't aired".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from anime_sh.app.playback import PlaybackService
from anime_sh.cli.main import has_aired
from anime_sh.domain.models import Anime, AnimeId, Status, Title
from anime_sh.infra.db.database import Database
from anime_sh.infra.db.library import SqliteLibrary
from anime_sh.tui.format import episode_air_label, next_episode_line


@pytest.fixture
async def library(tmp_path: Path):
    db = Database(tmp_path / "anime.db", migrations_dir="migrations")
    await db.connect()
    yield SqliteLibrary(db)
    await db.close()


def _show(*, status, episodes=12, next_ep=None, next_at=None):
    return Anime(
        id=AnimeId(anilist=1),
        title=Title(romaji="Show"),
        status=status,
        episode_count=episodes,
        next_airing_episode=next_ep,
        next_airing_at=next_at,
    )


async def _cached(library, *saves):
    for show in saves:
        await library.save_anime(show)
    return await library.get_anime(AnimeId(anilist=1))


async def test_a_finished_show_sheds_the_schedule_it_had_while_airing(library):
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    cached = await _cached(
        library,
        _show(status=Status.RELEASING, next_ep=10, next_at=soon),
        # The season ends; AniList now answers nextAiringEpisode: null.
        _show(status=Status.FINISHED),
    )

    assert cached.status is Status.FINISHED
    assert cached.next_airing_episode is None
    assert cached.next_airing_at is None


async def test_the_last_episodes_of_a_finished_show_count_as_aired(library):
    """The user-visible symptom: `play`/`prefetch` refusing a released episode.

    With episode 10 cached as "next to air", `has_aired` rejected 10, 11 and 12
    of a show that finished at 12 -- so the last quarter of the season was
    unreachable from a cached record.
    """
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    cached = await _cached(
        library,
        _show(status=Status.RELEASING, next_ep=10, next_at=soon),
        _show(status=Status.FINISHED),
    )

    assert [has_aired(cached, n) for n in (10, 11, 12)] == [True, True, True]


async def test_auto_next_runs_to_the_end_of_a_finished_show(library):
    """The other reader: `_has_next` gated auto-advance on the stale boundary,
    so finishing episode 8 of a 12-episode show quietly stopped the queue."""
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    cached = await _cached(
        library,
        _show(status=Status.RELEASING, next_ep=10, next_at=soon),
        _show(status=Status.FINISHED),
    )

    has_next = PlaybackService._has_next
    assert [has_next(None, cached, n) for n in (8.0, 9.0, 11.0)] == [True, True, True]
    assert has_next(None, cached, 12.0) is False  # the real end still stops it


async def test_a_finished_show_is_not_given_a_countdown(library):
    """The cosmetic half: two formatters read the schedule without checking
    `is_airing`, and would have printed a countdown to a date in the past."""
    passed = datetime.now(timezone.utc) - timedelta(days=200)
    cached = await _cached(
        library,
        _show(status=Status.RELEASING, next_ep=10, next_at=passed),
        _show(status=Status.FINISHED),
    )

    assert next_episode_line(cached) is None
    assert episode_air_label(cached, 11) is None


async def test_an_airing_show_still_gets_its_schedule_back(library):
    """The guard must not take the feature out with the bug: a show that is
    still airing is exactly the case the cache exists for."""
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    cached = await _cached(library, _show(status=Status.RELEASING, next_ep=5, next_at=soon))

    assert cached.next_airing_episode == 5
    assert cached.next_airing_at == soon
    # Explicit `now`: countdown truncates, so a wall-clock "2 days from now"
    # reads back as 1d 23h by the time the assertion runs.
    assert next_episode_line(cached, soon - timedelta(days=2)) == "Ep 5 in 2d 0h"


async def test_a_bare_save_still_cannot_wipe_a_live_schedule(library):
    """Why the write path keeps COALESCE, and why `status` joined it.

    Saving an Anime that carries no schedule must not clear a real one -- that
    guarantee already existed. Gating the read on `status` quietly put a second
    way to break it: a bare Anime's status is `UNKNOWN`, which used to overwrite
    `RELEASING` and so hid a schedule that was still there.
    """
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    cached = await _cached(
        library,
        _show(status=Status.RELEASING, next_ep=5, next_at=soon),
        Anime(id=AnimeId(anilist=1), title=Title(romaji="Show")),  # knows nothing
    )

    assert cached.status is Status.RELEASING
    assert cached.next_airing_episode == 5
    assert cached.next_airing_at == soon


# --------------------------------------------------------------------------- #
# The complementary case: the show is still RELEASING, but the date it is
# holding has come and gone. The read guard above only clears a schedule once a
# show is no longer airing, so nothing clears this one -- and `continue_watching`
# paints straight from the cached row with no refetch, so it survives for as
# long as the user does not open that show. Five readers each did the `- 1`
# inline and so each kept treating the newest *released* episode as unreleased.
# --------------------------------------------------------------------------- #
def _releasing(next_ep: int, next_at: datetime, episodes: int | None = 12) -> Anime:
    return _show(status=Status.RELEASING, episodes=episodes, next_ep=next_ep, next_at=next_at)


# Anchored to the wall clock, not a literal. `_has_next` and `has_aired` take
# no `now`, so they read the real clock -- a pinned NOW would leave FUTURE in
# the past within days and assert the opposite of what it says, which is the
# drift this very boundary exists to stop (and which `test_skip_autonext` had
# already quietly suffered). Offsets stay exact, so the countdowns below still
# format to the minute when `now=NOW` is passed explicitly.
NOW = datetime.now(timezone.utc)
FUTURE = _releasing(12, NOW + timedelta(days=2))   # 11 out, 12 still to come
PAST = _releasing(12, NOW - timedelta(days=2))     # 12 came out two days ago


def test_aired_through_counts_the_episode_once_its_time_has_passed():
    assert FUTURE.aired_through(NOW) == 11
    assert PAST.aired_through(NOW) == 12, "its air time passed; that episode is out"
    assert _show(status=Status.RELEASING).aired_through(NOW) is None, "no schedule, no answer"


def test_a_past_due_schedule_does_not_claim_you_are_caught_up():
    """The symptom with teeth. Episode 12 aired two days ago and is unwatched,
    and `waiting_subtitle` called that "caught up" -- which `continue_cells`
    renders dim and ranks below the actionable rows. The one episode the screen
    exists to surface was both greyed out and sorted to the bottom."""
    from anime_sh.tui.format import RANK_READY, continue_cells, waiting_subtitle
    from anime_sh.domain.models import WatchProgress

    assert waiting_subtitle(FUTURE, 11.0, NOW) == "caught up · Ep 12 in 2d 0h"
    assert waiting_subtitle(PAST, 11.0, NOW) is None, "episode 12 is out and unwatched"

    finished_11 = WatchProgress(
        anime_id=AnimeId(anilist=1), episode=11.0, position_s=0,
        duration_s=0, completed=True, updated_at=NOW,
    )
    row, plays = continue_cells(PAST, finished_11, NOW)
    assert plays == 12.0
    assert row.rank == RANK_READY and not row.dim, "a released episode must not be dimmed"


def test_auto_next_reaches_an_episode_that_has_just_aired():
    """`_has_next` stopped at `next_airing_episode - 1`, so for the whole stretch
    between an episode airing and the cached row being refreshed, auto-advance
    halted one episode short of what was sitting there."""
    has_next = PlaybackService._has_next
    assert has_next(None, FUTURE, 10.0) is True
    assert has_next(None, FUTURE, 11.0) is False, "12 is genuinely not out yet"
    assert has_next(None, PAST, 11.0) is True, "12 aired two days ago"
    assert has_next(None, PAST, 12.0) is False, "13 has no known date"


def test_play_does_not_refuse_an_episode_that_has_already_aired():
    assert [has_aired(FUTURE, n) for n in (11, 12)] == [True, False]
    assert [has_aired(PAST, n) for n in (12, 13)] == [True, False]


def test_the_counts_on_the_browse_rows_include_it_too():
    from anime_sh.tui.format import browse_cells, home_subtitle

    assert browse_cells(FUTURE, NOW).position == "11/12 eps"
    assert browse_cells(PAST, NOW).position == "12/12 eps", "12 of 12 have aired"
    assert "12/12 eps" in home_subtitle(PAST, NOW)


def test_a_countdown_stops_calling_a_stale_date_a_live_broadcast():
    """"airing now" was returned for any past timestamp, so a schedule nobody
    had refreshed in three weeks read exactly like one airing this minute."""
    from anime_sh.tui.format import countdown, next_episode_line

    assert countdown(NOW + timedelta(days=2), NOW) == "in 2d 0h"
    assert countdown(NOW - timedelta(minutes=20), NOW) == "airing now"
    assert countdown(NOW - timedelta(days=21), NOW) == "aired"
    assert next_episode_line(PAST, NOW) == "Ep 12 aired"


def test_a_projection_off_a_past_anchor_is_not_offered_at_all():
    """`episode_air_label` walked a weekly cadence forward from
    `next_airing_at`. Once that anchor is in the past every date it produces is
    derived from a stale one, so there is nothing honest to say."""
    assert episode_air_label(FUTURE, 12, NOW) == "airs in 2d 0h"
    assert episode_air_label(FUTURE, 13, NOW) == "airs in 9d 0h"
    assert episode_air_label(PAST, 12, NOW) is None, "already aired"
    assert episode_air_label(PAST, 13, NOW) is None, "no honest date from a stale anchor"
