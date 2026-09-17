"""A provider that cannot produce a stream must stop being tried first.

The breaker only ever heard about *matching*. anikoto matched instantly and
resolved nothing for five days from 13/09/2026 — its hosts had moved to an
encrypted payload — so it stayed "healthy", sorted first, and every play paid
the full fan-out into it before falling through to a provider that worked.
"""

from __future__ import annotations

import pytest

from anime_sh.app.playback import PlaybackService
from anime_sh.app.providers import ProviderManager
from anime_sh.domain.models import Audio
from anime_sh.infra.players import NullPlayer

from .fakes import FakeLibrary, FakeProvider, FakeResolver, make_anime

ANIME = make_anime()


class _SpyManager(ProviderManager):
    """The real manager, with the playback outcomes it is told about recorded."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded: list[tuple[str, bool]] = []

    async def record_playback(self, provider_name, *, playable):
        self.recorded.append((provider_name, playable))
        await super().record_playback(provider_name, playable=playable)


def _service(manager, resolvers):
    return PlaybackService(
        providers=manager,
        resolvers=resolvers,
        player=NullPlayer(),
        library=FakeLibrary(),
    )


def _manager(provider):
    return _SpyManager([provider], match_timeout_s=1, candidates_timeout_s=1)


async def _collect(service):
    return [item async for item in service._candidate_streams(ANIME, 1.0, Audio.SUB)]


async def test_a_provider_that_yields_a_stream_is_recorded_playable():
    manager = _manager(FakeProvider("probe", episodes_for={"probe-key": [1.0]}))
    service = _service(manager, [FakeResolver("fake", host="mp4upload")])
    assert await _collect(service)
    assert manager.recorded == [("probe", True)]


async def test_a_provider_whose_hosts_all_fail_is_recorded_unplayable():
    """The anikoto case: candidates offered, nothing resolvable."""
    manager = _manager(FakeProvider("probe", episodes_for={"probe-key": [1.0]}))
    service = _service(manager, [FakeResolver("fake", host="mp4upload", behaviour="fail")])
    assert await _collect(service) == []
    assert manager.recorded == [("probe", False)]


async def test_a_provider_with_no_candidates_is_a_miss_not_a_failure():
    """A provider that simply does not carry this episode is still healthy —
    otherwise every niche title would trip every breaker."""
    class _NoCandidates(FakeProvider):
        async def candidates(self, episode):
            return []

    manager = _manager(_NoCandidates("probe", episodes_for={"probe-key": [1.0]}))
    service = _service(manager, [FakeResolver("fake", host="mp4upload")])
    assert await _collect(service) == []
    assert manager.recorded == []


async def test_success_is_recorded_even_when_the_caller_stops_at_the_first_stream():
    """Callers break out as soon as they have something to play, so anything
    recorded after the loop would never run on the one path that matters."""
    manager = _manager(FakeProvider("probe", episodes_for={"probe-key": [1.0]}))
    service = _service(manager, [FakeResolver("fake", host="mp4upload")])
    async for _ in service._candidate_streams(ANIME, 1.0, Audio.SUB):
        break
    assert manager.recorded == [("probe", True)]


async def test_a_manager_without_the_hook_still_plays():
    """The recording is best-effort: a bookkeeping gap must never cost the
    episode."""

    class _Old(ProviderManager):
        record_playback = None

    manager = _Old(
        [FakeProvider("probe", episodes_for={"probe-key": [1.0]})],
        match_timeout_s=1,
        candidates_timeout_s=1,
    )
    service = _service(manager, [FakeResolver("fake", host="mp4upload")])
    assert await _collect(service)
