"""`anime doctor --streams` — "can I actually watch, from *this* connection?"

The nightly canary cannot answer that for a user: it runs from a datacenter IP
where anizone serves a Cloudflare interstitial it never shows a home
connection. So this check has to run on the user's machine, through the app's
own resolve path rather than a reimplementation of it.
"""

from __future__ import annotations

import asyncio

import pytest

from anime_sh.cli import container as container_mod
from anime_sh.cli import doctor as doctor_mod
from anime_sh.domain.errors import NoStreamsFound, ProviderError
from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Audio,
    Episode,
    ProviderRef,
    Quality,
    SourceOption,
    Stream,
    StreamKind,
    Title,
)

ANIME = Anime(id=AnimeId(anilist=154587), title=Title(romaji="Frieren"))


def _option(provider: str) -> SourceOption:
    return SourceOption(
        provider=provider, anime_key="1", title="Frieren",
        episode_count=28, audio=Audio.SUB, confidence=1.0,
    )


class _Resolved:
    """Shaped like ResolvedPlayback — doctor only reads `.stream.url`."""

    def __init__(self, episode, stream, resume_s):
        self.episode, self.stream, self.resume_s = episode, stream, resume_s


def _resolved(url="https://cdn.example.test/master.m3u8"):
    ref = ProviderRef(provider="p", anime_key="1", audio=Audio.SUB)
    episode = Episode(anime_id=ANIME.id, number=1.0, provider_ref=ref, episode_key="1")
    stream = Stream(url=url, kind=StreamKind.HLS, quality=Quality.UNKNOWN)
    return _Resolved(episode, stream, 0)


class _Search:
    def __init__(self, anime=ANIME, error=None):
        self._anime, self._error = anime, error

    async def best_match(self, query):
        if self._error:
            raise self._error
        return self._anime


class _Playback:
    def __init__(self, options, outcomes):
        self._options, self._outcomes = options, outcomes

    async def list_sources(self, anime, **kw):
        if isinstance(self._options, Exception):
            raise self._options
        return self._options

    async def resolve(self, anime, episode, *, source=None, **kw):
        outcome = self._outcomes[source.provider]
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "hang":
            await asyncio.sleep(60)
        return outcome


class _Container:
    def __init__(self, search, playback):
        self.search, self.playback = search, playback
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture
def container(monkeypatch):
    holder = {}

    def install(search, playback):
        c = _Container(search, playback)
        holder["c"] = c
        # doctor imports build_container inside the function, so patch it at
        # the module it actually comes from.
        monkeypatch.setattr(container_mod, "build_container", lambda *a, **k: c)
        return c

    holder["install"] = install
    return holder


def _run(checks_coro):
    return asyncio.run(checks_coro)


def test_reports_each_provider_separately(container):
    c = container["install"](
        _Search(),
        _Playback(
            [_option("anikoto"), _option("hianime"), _option("anikoto")],
            {"anikoto": NoStreamsFound("nope"), "hianime": _resolved()},
        ),
    )
    checks = _run(doctor_mod._check_streams())
    assert [(x.name, x.ok) for x in checks] == [
        ("stream anikoto", False),
        ("stream hianime", True),
    ]
    assert "no host played" in checks[0].detail
    assert "cdn.example.test" in checks[1].detail
    assert c.closed, "the container must be closed even on the happy path"


def test_a_metadata_outage_is_not_a_verdict_on_any_provider(container):
    """Same rule the canary learned: if the identity lookup fails, no provider
    was contacted, so none of them can be reported as broken."""
    container["install"](
        _Search(error=ProviderError("AniList down")), _Playback([], {})
    )
    checks = _run(doctor_mod._check_streams())
    assert len(checks) == 1
    assert checks[0].name == "streams" and not checks[0].ok
    assert "nothing checked" in checks[0].detail


def test_a_slow_provider_is_bounded_not_waited_on(container, monkeypatch):
    monkeypatch.setattr(doctor_mod, "STREAM_CHECK_TIMEOUT_S", 0.05)
    container["install"](
        _Search(),
        _Playback([_option("slow")], {"slow": "hang"}),
    )
    checks = _run(doctor_mod._check_streams())
    assert not checks[0].ok and "timed out" in checks[0].detail


def test_the_container_is_closed_even_when_a_provider_raises(container):
    c = container["install"](
        _Search(), _Playback([_option("boom")], {"boom": RuntimeError("kaboom")})
    )
    checks = _run(doctor_mod._check_streams())
    assert not checks[0].ok and "kaboom" in checks[0].detail
    assert c.closed
