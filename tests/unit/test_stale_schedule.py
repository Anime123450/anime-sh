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
