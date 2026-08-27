"""What the app reports about plugins has to match what it will actually load.

`doctor` exists so a user — and a bug report — can answer "which providers
loaded" without being asked. It called `registry.load_providers()` with no
arguments, ignoring `providers.disabled` in config, so it named a provider as
loaded that nothing would ever call. `anime providers ls` had the same bug.

A diagnostic that lies is worse than no diagnostic: it sends you looking for the
problem somewhere other than where it is.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from anime_sh.cli import doctor as doctor_mod


@dataclass
class _Plugin:
    name: str
    priority: int = 0


class _Cfg:
    """Just the shape `_check_plugins` reads."""

    class _Section:
        def __init__(self, disabled): self.disabled = disabled

    def __init__(self, providers=(), resolvers=()):
        self.providers = self._Section(list(providers))
        self.resolvers = self._Section(list(resolvers))


@pytest.fixture
def registry(monkeypatch):
    """A registry that honours `disabled`, like the real one."""
    installed_p = [_Plugin("anizone", 90), _Plugin("anikoto", 85)]
    installed_r = [_Plugin("generic"), _Plugin("megaplay")]

    def load_providers(*, disabled=(), **kw):
        return [p for p in installed_p if p.name not in set(disabled)]

    def load_resolvers(*, disabled=(), **kw):
        return [r for r in installed_r if r.name not in set(disabled)]

    monkeypatch.setattr(doctor_mod.registry, "load_providers", load_providers)
    monkeypatch.setattr(doctor_mod.registry, "load_resolvers", load_resolvers)


def _check(cfg):
    return {c.name: c for c in doctor_mod._check_plugins(cfg)}


def _active(detail: str) -> str:
    """The names doctor claims are in use, i.e. everything before the
    "(... disabled in config)" tail.

    Asserting on the whole string is not enough and was the first mistake here:
    `startswith("anikoto")` passed against the bug, because the unfiltered list
    is "anikoto, anizone" and also starts with anikoto.
    """
    return detail.split("  (")[0].strip()


def test_a_disabled_provider_is_not_reported_as_loaded(registry):
    """The regression test. With `disabled = ["anizone"]` in config, doctor used
    to print `providers: anizone, anikoto`."""
    providers = _check(_Cfg(providers=["anizone"]))["providers"]
    assert _active(providers.detail) == "anikoto", providers.detail
    assert providers.ok


def test_a_disabled_provider_is_still_named_as_disabled(registry):
    """Dropping it silently turns "why is anizone never used?" into a mystery,
    which is the question doctor is supposed to answer."""
    detail = _check(_Cfg(providers=["anizone"]))["providers"].detail
    assert "(anizone disabled in config)" in detail, detail


def test_no_active_provider_is_a_failure_not_a_clean_bill_of_health(registry):
    """Disabling every provider leaves nothing able to find episodes. Doctor
    reported "anime-sh core looks healthy" — the one situation it exists to
    catch."""
    check = _check(_Cfg(providers=["anizone", "anikoto"]))["providers"]
    assert not check.ok
    assert "none active" in check.detail


def test_nothing_disabled_reads_cleanly(registry):
    """The common case must not grow noise about an empty disabled list."""
    detail = _check(_Cfg())["providers"].detail
    assert detail == "anikoto, anizone"
    assert "disabled" not in detail


def test_a_missing_config_does_not_break_the_check(registry):
    """`run_doctor` tolerates an unreadable config and passes None. Doctor has to
    keep working precisely then — a broken config is when it is needed most."""
    check = _check(None)["providers"]
    assert check.ok
    assert check.detail == "anikoto, anizone"
