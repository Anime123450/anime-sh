"""AniSkip source parsing + PlaybackService skip augmentation."""

from __future__ import annotations

import pytest

from anime_sh.app.playback import PlaybackService
from anime_sh.app.providers import ProviderManager
from anime_sh.domain.models import (
    Anime, AnimeId, Quality, SkipRange, SkipTimes, Stream, StreamKind, Title,
)
from anime_sh.infra.http import HttpError
from anime_sh.infra.skiptimes.aniskip import AniSkipSource

from .fakes import FakeLibrary


class _FakeHttp:
    """get_json returns a canned AniSkip response, or raises for the miss case."""

    def __init__(self, response=None, raises=False):
        self._response = response
        self._raises = raises
        self.calls = 0

    async def get_json(self, url, *, params=None, headers=None):
        self.calls += 1
        if self._raises:
            raise HttpError("404 not found")
        return self._response


_FOUND = {"found": True, "results": [
    {"skipType": "op", "interval": {"startTime": 34.5, "endTime": 124.5}},
    {"skipType": "ed", "interval": {"startTime": 1330.5, "endTime": 1421.2}},
]}


async def test_parses_op_and_ed():
    src = AniSkipSource(http=_FakeHttp(_FOUND))
    skips = await src.for_episode(52991, 1.0, episode_length=1440)
    assert skips is not None
    assert skips.op == SkipRange(start_s=34, end_s=124)
    assert skips.ed == SkipRange(start_s=1330, end_s=1421)


async def test_no_mal_id_short_circuits_without_request():
    http = _FakeHttp(_FOUND)
    src = AniSkipSource(http=http)
    assert await src.for_episode(None, 1.0, episode_length=1440) is None
    assert http.calls == 0  # never hit the network


async def test_not_found_returns_none():
    src = AniSkipSource(http=_FakeHttp({"found": False, "results": []}))
    assert await src.for_episode(1, 1.0, episode_length=1440) is None


async def test_http_error_is_swallowed():
    src = AniSkipSource(http=_FakeHttp(raises=True))
    assert await src.for_episode(1, 1.0, episode_length=1440) is None


async def test_zero_length_interval_ignored():
    resp = {"found": True, "results": [
        {"skipType": "op", "interval": {"startTime": 10, "endTime": 10}},  # empty
    ]}
    src = AniSkipSource(http=_FakeHttp(resp))
    assert await src.for_episode(1, 1.0, episode_length=1440) is None


# -- PlaybackService augmentation ------------------------------------------- #
class _FakeSkipSource:
    def __init__(self, skips):
        self._skips = skips
        self.calls = 0

    async def for_episode(self, mal_id, episode, *, episode_length):
        self.calls += 1
        return self._skips


def _svc(skip_source, *, skip_intro=True, skip_outro=False):
    return PlaybackService(
        providers=ProviderManager([]),
        resolvers=[],
        player=None,
        library=FakeLibrary(),
        skip_intro=skip_intro,
        skip_outro=skip_outro,
        skip_source=skip_source,
    )


def _stream(skip_times=None):
    return Stream(url="https://x/v.m3u8", kind=StreamKind.HLS,
                  quality=Quality.UNKNOWN, skip_times=skip_times)


def _anime(mal=52991, duration=24):
    return Anime(id=AnimeId(anilist=1, mal=mal), title=Title(romaji="X"),
                 duration_min=duration)


async def test_augment_fills_missing_skips():
    skips = SkipTimes(op=SkipRange(30, 120))
    svc = _svc(_FakeSkipSource(skips))
    out = await svc._augment_skips(_stream(), _anime(), 1.0)
    assert out.skip_times == skips


async def test_augment_does_not_override_provider_skips():
    provider_skips = SkipTimes(op=SkipRange(1, 2))
    fake = _FakeSkipSource(SkipTimes(op=SkipRange(30, 120)))
    svc = _svc(fake)
    out = await svc._augment_skips(_stream(provider_skips), _anime(), 1.0)
    assert out.skip_times == provider_skips  # provider data wins
    assert fake.calls == 0  # and AniSkip was never consulted


async def test_augment_skipped_when_no_mal_id():
    fake = _FakeSkipSource(SkipTimes(op=SkipRange(30, 120)))
    svc = _svc(fake)
    out = await svc._augment_skips(_stream(), _anime(mal=None), 1.0)
    assert out.skip_times is None and fake.calls == 0


async def test_augment_skipped_when_skip_disabled():
    fake = _FakeSkipSource(SkipTimes(op=SkipRange(30, 120)))
    svc = _svc(fake, skip_intro=False, skip_outro=False)
    out = await svc._augment_skips(_stream(), _anime(), 1.0)
    assert out.skip_times is None and fake.calls == 0
