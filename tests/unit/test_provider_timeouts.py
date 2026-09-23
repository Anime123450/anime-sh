"""Both provider timeouts come from config, not just one of them.

`providers.timeout_s` reached the candidates step and nothing else. The match
budget stayed hardcoded at four seconds, so someone on a slow connection who
raised the setting still had matching cut off — and matching runs first, on
every search, so nothing downstream ever got the chance to use the longer
budget they had asked for.
"""

from __future__ import annotations

import asyncio

import pytest

from anime_sh.app.providers import ProviderManager
from anime_sh.config.schema import ProvidersConfig
from anime_sh.domain.models import Anime, AnimeId, Audio, Title

ANIME = Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren"))


class _SlowProvider:
    """Takes `delay` seconds to answer a match."""

    name = "slow"
    priority = 10
    api_version = 1

    def __init__(self, delay: float):
        self.delay = delay
        self.matched = False

    async def match(self, anime, audio):
        await asyncio.sleep(self.delay)
        self.matched = True
        from anime_sh.domain.models import ProviderRef

        return ProviderRef(provider=self.name, anime_key="1", audio=audio)

    async def find_sources(self, anime, audio):
        await asyncio.sleep(self.delay)
        self.matched = True
        from anime_sh.domain.models import SourceOption

        return [SourceOption(provider=self.name, anime_key="1", title="Frieren",
                             episode_count=12, audio=audio, confidence=1.0)]


def _resolve(manager):
    return asyncio.run(manager.resolve_sources(ANIME, Audio.SUB))


def test_the_default_match_budget_still_cuts_off_a_slow_provider():
    provider = _SlowProvider(delay=0.4)
    manager = ProviderManager([provider], match_timeout_s=0.05)
    assert _resolve(manager) == [], "should have timed out"


def test_a_longer_match_budget_lets_the_same_provider_through():
    """The whole point of the setting: raising it has to actually help."""
    provider = _SlowProvider(delay=0.2)
    manager = ProviderManager([provider], match_timeout_s=2.0)
    assert len(_resolve(manager)) == 1
    assert provider.matched


# -- the setting -------------------------------------------------------------#
def test_both_timeouts_exist_in_config():
    cfg = ProvidersConfig()
    assert cfg.timeout_s == 8.0
    assert cfg.match_timeout_s == 4.0, "the previously hardcoded value"


@pytest.mark.parametrize("field", ["timeout_s", "match_timeout_s"])
def test_a_timeout_must_be_positive(field):
    """Zero would mean every provider times out instantly and nothing ever
    plays — the same class of mistake as `parallel=0` slicing the provider
    list to nothing."""
    with pytest.raises(Exception):
        ProvidersConfig(**{field: 0})


def test_the_container_passes_both():
    import inspect

    from anime_sh.cli import container

    source = inspect.getsource(container.Container.provider_manager.func)
    assert "candidates_timeout_s=self.config.providers.timeout_s" in source
    assert "match_timeout_s=self.config.providers.match_timeout_s" in source
