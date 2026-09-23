"""The shipped default must not freeze the provider order.

`providers.preferred` defaulted to ["anizone", "anikoto"], which only restated
the built-in priorities — so the order lived in two places, and a provider's
`priority` was dead code for anyone on a default config. That stopped being
merely redundant when anikoto's hosts moved to an encrypted payload: demoting
it did nothing, and every default install kept trying the provider that cannot
play anything before the one that can. A fresh breaker has nothing recorded
yet, so a cold install paid for that on its first plays.

The knob belongs to the user. The default order belongs next to the providers.
"""

from __future__ import annotations

from anime_sh.app.providers import ProviderManager
from anime_sh.config.schema import ProvidersConfig

from .fakes import FakeProvider


def _order(preferred: list[str]) -> list[str]:
    providers = [
        FakeProvider("anizone", priority=90),
        FakeProvider("hianime", priority=80),
        FakeProvider("anikoto", priority=70),
    ]
    return [p.name for p in ProviderManager(providers, preferred=preferred).providers]


def test_the_default_config_defers_to_the_built_in_priorities():
    """Not "the default happens to equal the priorities" — the default must be
    empty, so that changing a priority actually changes what users get."""
    assert ProvidersConfig().preferred == []
    assert _order(ProvidersConfig().preferred) == _order([])


def test_the_knob_still_works_when_a_user_sets_it():
    assert _order(["anikoto"])[0] == "anikoto"


# -- the ordering the providers themselves declare --------------------------- #
def test_the_provider_that_can_play_outranks_the_one_that_cannot():
    """anikoto has produced no playable stream since 13/09/2026 and its hosts
    are not coming back; hianime is what actually serves. Ranking is a claim
    about which provider to spend a cold install's first attempts on."""
    from anime_sh.providers.anikoto.provider import AnikotoProvider
    from anime_sh.providers.hianime.provider import HianimeProvider

    assert HianimeProvider.priority > AnikotoProvider.priority
