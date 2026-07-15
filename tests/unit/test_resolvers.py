"""Resolvers: quality mapping, generic passthrough, and AllAnime clock parsing."""

from __future__ import annotations

from anime_sh.domain.models import Quality, StreamCandidate, StreamKind
from anime_sh.resolvers.allanime.clock import AllAnimeClockResolver
from anime_sh.resolvers.generic import GenericStreamResolver
from anime_sh.resolvers.quality import kind_from_url, quality_from_str


def test_quality_from_str():
    assert quality_from_str("1080") == Quality.Q1080
    assert quality_from_str("1080p") == Quality.Q1080
    assert quality_from_str("4K") == Quality.Q2160
    assert quality_from_str(None) == Quality.UNKNOWN
    assert quality_from_str("weird") == Quality.UNKNOWN


def test_kind_from_url():
    assert kind_from_url("https://x/a.m3u8") == StreamKind.HLS
    assert kind_from_url("https://x/a.mp4?token=1") == StreamKind.MP4
    assert kind_from_url("https://x/a.mpd") == StreamKind.DASH


def test_generic_handles_and_resolves():
    r = GenericStreamResolver()
    assert r.handles(StreamCandidate(host="direct", url="https://x/a.m3u8"))
    assert not r.handles(StreamCandidate(host="x", url="https://x/embed/page"))


async def test_generic_passthrough():
    r = GenericStreamResolver()
    cand = StreamCandidate(host="direct", url="https://x/a.mp4", quality_hint="720")
    streams = await r.resolve(cand)
    assert streams[0].kind == StreamKind.MP4
    assert streams[0].quality == Quality.Q720


class _FakeHttp:
    def __init__(self, payload):
        self._payload = payload

    async def get_json(self, url, *, params=None, headers=None):
        return self._payload


def test_clock_handles():
    r = AllAnimeClockResolver(http=_FakeHttp({}))
    assert r.handles(StreamCandidate(host="Luf", url="https://api.allanime.day/apivtwo/clock.json?id=1"))
    assert not r.handles(StreamCandidate(host="x", url="https://x/a.m3u8"))


async def test_clock_resolves_links_to_streams():
    payload = {
        "links": [
            {"link": "https://cdn/1080.m3u8", "resolutionStr": "1080"},
            {"link": "https://cdn/720.mp4", "resolutionStr": "720"},
        ]
    }
    r = AllAnimeClockResolver(http=_FakeHttp(payload))
    cand = StreamCandidate(host="Luf", url="https://api.allanime.day/apivtwo/clock.json?id=1")
    streams = await r.resolve(cand)
    assert {s.quality for s in streams} == {Quality.Q1080, Quality.Q720}
    assert streams[0].kind == StreamKind.HLS
