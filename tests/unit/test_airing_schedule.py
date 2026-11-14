"""Upcoming episodes come from AniList's real schedule, not only a guess.

A show still airing often has no announced episode total, and the detail screen
listed only what the source already carried: no sign of what is still to come,
or when. AniList publishes the remaining schedule; the single-show query asks
for it and the label uses its exact times.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anime_sh.domain.models import Anime, AnimeId, Status, Title
from anime_sh.infra.metadata.anilist import _GET_Q, _to_anime
from anime_sh.tui.format import episode_air_label

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def test_the_single_show_query_asks_for_the_schedule():
    assert "airingSchedule" in _GET_Q


def test_the_schedule_is_parsed_in_order():
    t7 = int((NOW + timedelta(hours=4)).timestamp())
    t8 = t7 + 7 * 86400
    a = _to_anime({"id": 1, "title": {"romaji": "X"}, "status": "RELEASING",
                   "nextAiringEpisode": {"episode": 7, "airingAt": t7},
                   "airingSchedule": {"nodes": [{"episode": 8, "airingAt": t8},
                                                {"episode": 7, "airingAt": t7}]}})
    assert [e for e, _ in a.airing_schedule] == [7, 8]
    assert a.airing_schedule[1][1] == datetime.fromtimestamp(t8, tz=timezone.utc)


def test_a_record_without_a_schedule_still_builds():
    assert _to_anime({"id": 1, "title": {"romaji": "X"}}).airing_schedule == ()


def test_the_label_uses_the_scheduled_time_not_a_weekly_guess():
    # A break week: ep 9 airs 14 days after ep 8, not 7.
    sched = ((7, NOW + timedelta(days=1)), (8, NOW + timedelta(days=8)),
             (9, NOW + timedelta(days=22)))
    a = Anime(id=AnimeId(anilist=1), title=Title(romaji="X"), status=Status.RELEASING,
              next_airing_episode=7, next_airing_at=sched[0][1], airing_schedule=sched)
    assert episode_air_label(a, 9, NOW) == "airs in 22d 0h"
    # Past the published schedule, the weekly projection is still the fallback.
    assert episode_air_label(a, 10, NOW) == "airs in 29d 0h"
    # An episode whose scheduled time has passed has aired: no label.
    gone = ((7, NOW - timedelta(hours=1)), (8, NOW + timedelta(days=6)))
    b = Anime(id=AnimeId(anilist=1), title=Title(romaji="X"), status=Status.RELEASING,
              next_airing_episode=7, next_airing_at=gone[0][1], airing_schedule=gone)
    assert episode_air_label(b, 7, NOW) is None
    assert episode_air_label(b, 8, NOW) == "airs in 6d 0h"
