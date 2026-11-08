"""anikoto's only stream host (megaplay) now serves an encrypted playlist that
no resolver can open, so a default install must not try it."""

from anime_sh.config.schema import Config


def test_anikoto_disabled_by_default():
    assert "anikoto" in Config().providers.disabled
