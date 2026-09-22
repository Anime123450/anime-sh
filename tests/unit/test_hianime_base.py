"""The hianime host is a setting, not a constant.

HiAnime runs behind a rotating set of mirrors, and which of them answer depends
on where you are: from the machine this was written on, every mirror but one is
blocked by the ISP, and that could as easily be the other way round for someone
else. "This domain stopped working for me today" should not need a release.
"""

from __future__ import annotations

import pytest

from anime_sh.domain.models import Audio
from anime_sh.providers.hianime import provider as mod
from anime_sh.providers.hianime.provider import HianimeProvider, configured_base


class _Http:
    """Records every URL asked for, and answers with empty markup."""

    def __init__(self):
        self.urls: list[str] = []

    async def get_text(self, url, *, params=None, headers=None):
        self.urls.append(url)
        return ""

    async def get_json(self, url, *, params=None, headers=None):
        self.urls.append(url)
        return {"status": True, "html": ""}

    async def aclose(self):
        pass


def _config(value, *, broken=False):
    class _Providers:
        hianime_base = value

    class _Config:
        providers = _Providers()

    def load_config():
        if broken:
            raise RuntimeError("config is unreadable")
        return _Config()

    return load_config


@pytest.fixture
def fake_config(monkeypatch):
    def install(value, *, broken=False):
        import anime_sh.config as config_mod

        monkeypatch.setattr(config_mod, "load_config", _config(value, broken=broken))

    return install


def test_the_default_is_the_built_in_host(fake_config):
    fake_config("")
    assert configured_base() == mod.BASE


def test_a_configured_host_wins(fake_config):
    fake_config("https://hianime.example")
    assert configured_base() == "https://hianime.example"


def test_a_trailing_slash_does_not_become_a_double_slash(fake_config):
    """Paths are joined as f"{base}{path}", so a trailing slash would produce
    https://host//search — which some hosts 404."""
    fake_config("https://hianime.example/")
    assert configured_base() == "https://hianime.example"


def test_an_unreadable_config_falls_back_rather_than_failing(fake_config):
    """A provider that cannot start because a setting is malformed is worse
    than one that uses its default host."""
    fake_config("", broken=True)
    assert configured_base() == mod.BASE


async def test_every_request_goes_to_the_configured_host(fake_config):
    fake_config("https://mirror.example")
    http = _Http()
    provider = HianimeProvider(http=http)

    from anime_sh.domain.models import AnimeId, Episode, ProviderRef, Title, Anime

    anime = Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren"))
    await provider.find_sources(anime, Audio.SUB)
    ref = ProviderRef(provider="hianime", anime_key="481", audio=Audio.SUB)
    await provider.episodes(ref, anime.id)
    await provider.candidates(
        Episode(anime_id=anime.id, number=1.0, provider_ref=ref, episode_key="9227")
    )

    assert http.urls, "the provider made no requests at all"
    stray = [u for u in http.urls if not u.startswith("https://mirror.example/")]
    assert not stray, f"these still point at the built-in host: {stray}"


async def test_an_explicit_base_beats_the_config(fake_config):
    """Constructing one directly is what a test or a plugin does; it should not
    have to change global config to pick a host."""
    fake_config("https://from-config.example")
    http = _Http()
    provider = HianimeProvider(http=http, base="https://explicit.example/")
    from anime_sh.domain.models import AnimeId, Title, Anime

    await provider.find_sources(
        Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren")), Audio.SUB
    )
    assert http.urls[0].startswith("https://explicit.example/")
