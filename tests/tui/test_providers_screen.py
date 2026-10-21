"""Sources: which providers and resolvers get used, on the Settings screen.

`p` used to open a provider modal; it now opens Settings on the Sources tab.
These are that modal's guarantees, kept.
"""

from __future__ import annotations

import pytest

from anime_sh.app.settings import SettingsService
from anime_sh.config.loader import load_config
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.screens.providers import ProviderItem, ProvidersScreen
from anime_sh.tui.screens.settings import SettingsScreen, ToggleRow

from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _noop


def _app(config):
    return AnimeShApp(
        TuiServices(search=FakeSearch(), metadata=FakeMetadata(),
                    library=FakeLibrary(), playback=FakePlayback(), aclose=_noop,
                    settings=SettingsService(config)),
        theme="midnight",
    )


@pytest.fixture
def installed(monkeypatch):
    def _set(rows):
        monkeypatch.setattr(SettingsScreen, "_installed_providers",
                            staticmethod(lambda: rows))
        monkeypatch.setattr(SettingsScreen, "_installed_resolvers",
                            staticmethod(lambda: []))
    return _set


def test_the_key_is_in_the_footer_where_it_can_be_found():
    """The whole point: a setting nobody can find is a setting nobody has."""
    shown = {
        b.key for b in AnimeShApp.BINDINGS
        if hasattr(b, "key") and getattr(b, "show", False)
    }
    assert "p" in shown, "providers is bound but hidden from the footer"


def test_the_old_names_still_mean_the_sources_tab():
    assert ProviderItem is ToggleRow
    assert issubclass(ProvidersScreen, SettingsScreen)
    assert ProvidersScreen().category == "sources"


async def _providers(app, pilot):
    await pilot.pause()
    await pilot.press("p")
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return [r for r in app.screen.query(ToggleRow) if r.group == "providers"]


async def test_it_lists_what_is_installed_with_its_state(installed, tmp_path):
    """Priority order, because that is the order they are actually tried in."""
    installed([("anizone", 90), ("anikoto", 85)])
    config = tmp_path / "config.toml"
    config.write_text('[providers]\ndisabled = ["anizone"]\n', encoding="utf-8")
    app = _app(config)
    async with app.run_test(size=(120, 40)) as pilot:
        rows = await _providers(app, pilot)
        assert [r.name_ for r in rows] == ["anizone", "anikoto"]
        assert [r.enabled for r in rows] == [False, True]


async def test_turning_one_off_is_written_to_the_config(installed, tmp_path):
    installed([("anizone", 90), ("anikoto", 85)])
    config = tmp_path / "config.toml"
    app = _app(config)
    async with app.run_test(size=(120, 40)) as pilot:
        rows = await _providers(app, pilot)
        await app.screen.activate(rows[0])
        await pilot.pause()
        assert rows[0].enabled is False
    assert load_config(config).providers.disabled == ["anizone"]


async def test_the_last_provider_cannot_be_turned_off(installed, tmp_path):
    """A configuration with every provider off cannot find a single episode.
    Refused here, as a choice, rather than surfacing later as "no sources"."""
    installed([("anikoto", 85)])
    config = tmp_path / "config.toml"
    app = _app(config)
    async with app.run_test(size=(120, 40)) as pilot:
        (only,) = await _providers(app, pilot)
        await app.screen.activate(only)
        await pilot.pause()
        assert only.enabled is True, "the last provider was switched off"
    assert not config.exists()


async def test_a_plugin_that_will_not_import_does_not_take_the_picker_down(
    monkeypatch, tmp_path,
):
    """The picker is where you would go to turn a broken plugin off, so it has
    to open when one is broken."""
    def boom():
        raise RuntimeError("entry point blew up")

    import anime_sh.infra.registry as registry
    monkeypatch.setattr(registry, "load_providers", boom)

    assert SettingsScreen._installed_providers() == []

    app = _app(tmp_path / "config.toml")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(ProvidersScreen())
        await pilot.pause()
        assert app.screen.query_one("#settings-list") is not None
