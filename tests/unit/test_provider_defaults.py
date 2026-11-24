"""Every shipped provider is on by default now that both encrypted hosts
(megaplay for anikoto, flixcloud for reanime) are decrypted locally."""

from anime_sh.config.schema import Config


def test_no_provider_disabled_by_default():
    assert Config().providers.disabled == []
