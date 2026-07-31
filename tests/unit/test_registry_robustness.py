"""A third-party plugin must never be able to take anime-sh down."""

from __future__ import annotations

from anime_sh.infra import registry


class _EP:
    def __init__(self, name, loader):
        self.name = name
        self._loader = loader

    def load(self):
        return self._loader()


def _good_provider():
    class Good:
        name = "good"
        priority = 10
        api_version = registry.API_VERSION

    return Good


def test_broken_plugins_are_skipped_and_the_good_ones_still_load(monkeypatch, caplog):
    def explodes_on_import():
        raise ImportError("no module named 'nope'")

    def explodes_on_construction():
        class Boom:
            def __init__(self):
                raise RuntimeError("bad plugin")

        return Boom

    def wrong_api_version():
        class Old:
            name = "old"
            api_version = registry.API_VERSION + 99

        return Old

    eps = [
        _EP("importerror", explodes_on_import),
        _EP("constructor", explodes_on_construction),
        _EP("outdated", wrong_api_version),
        _EP("good", _good_provider),
    ]
    monkeypatch.setattr(registry, "entry_points", lambda group: eps)

    loaded = registry._load_group("anime_sh.providers")
    assert [p.name for p in loaded] == ["good"], "a broken plugin took others with it"


def test_disabled_plugins_are_not_even_loaded(monkeypatch):
    loads: list[str] = []

    def tracked():
        loads.append("good")
        return _good_provider()

    monkeypatch.setattr(registry, "entry_points", lambda group: [_EP("good", tracked)])
    assert registry._load_group("anime_sh.providers", disabled=["good"]) == []
    assert loads == [], "a disabled plugin was still imported"
