"""A show that hasn't aired has nothing to play.

Tensei Shitara Slime Datta Ken 4th Season Part 3 (NOT_YET_RELEASED, 2027)
listed 24 playable episodes on 2026-11-17: hianime and anikoto both matched it
to "That Time I Got Reincarnated as a Slime Season 4" (the 24-episode Parts
1 & 2) at 0.933, so Enter would have played the previous season and recorded
the progress against the new one.
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from anime_sh.app.playback import PlaybackService
from anime_sh.app.providers import ProviderManager
from anime_sh.domain.errors import NoStreamsFound
from anime_sh.domain.models import Status
from anime_sh.infra.players import NullPlayer

from .fakes import FakeLibrary, FakeProvider, FakeResolver, make_anime

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)


def _unreleased(**kw):
    return dataclasses.replace(make_anime(title="Slime 4th Season Part 3"),
                               status=Status.NOT_YET_RELEASED, year=2027, **kw)


def _service(providers):
    return PlaybackService(
        providers=ProviderManager(providers, match_timeout_s=1, candidates_timeout_s=1),
        resolvers=[FakeResolver("mp4", host="mp4upload")],
        player=NullPlayer(),
        library=FakeLibrary(),
    )


def test_a_show_that_has_not_started_has_not_released():
    assert not _unreleased().has_released(NOW)


def test_released_statuses_count_as_released():
    for status in (Status.RELEASING, Status.FINISHED, Status.HIATUS,
                   Status.CANCELLED, Status.UNKNOWN):
        assert dataclasses.replace(make_anime(), status=status).has_released(NOW)


def test_a_stale_status_loses_to_a_schedule_that_has_come_true():
    """AniList flips the status a while after episode 1 airs; the schedule
    already says it is out, and that must not lock the episode away."""
    aired = _unreleased(next_airing_episode=1, next_airing_at=NOW - timedelta(hours=1))
    assert aired.has_released(NOW)
    ahead = _unreleased(next_airing_episode=1, next_airing_at=NOW + timedelta(days=3))
    assert not ahead.has_released(NOW)


async def test_no_provider_is_asked_about_an_unreleased_show():
    provider = FakeProvider("hianime")  # matches anything, like the real search did
    svc = _service([provider])
    assert await svc.available_episodes(_unreleased()) == []
    assert await svc.list_sources(_unreleased()) == []
    assert provider.calls == []


async def test_playing_an_unreleased_show_says_so():
    svc = _service([FakeProvider("hianime")])
    with pytest.raises(NoStreamsFound, match="not been released"):
        await svc.resolve(_unreleased(), 1.0)
