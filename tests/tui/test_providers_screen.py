"""The provider picker.

Which sources anime-sh may search was previously only reachable through
`anime providers enable|disable` — a thing you had to know existed, and the
setting most worth reaching when nothing will play.
"""

from __future__ import annotations

import pytest
from textual.widgets import ListView

from anime_sh.tui.app import AnimeShApp
from anime_sh.tui.screens.providers import ProviderItem, ProvidersScreen

from .test_app import _make_app


def _keys(bindings):
    return {b.key if hasattr(b, "key") else b[0] for b in bindings}


def test_the_key_is_in_the_footer_where_it_can_be_found():
    """The whole point: a setting nobody can find is a setting nobody has."""
    shown = {
        b.key for b in AnimeShApp.BINDINGS
        if hasattr(b, "key") and getattr(b, "show", False)
    }
    assert "p" in shown, "providers is bound but hidden from the footer"


async def test_it_lists_what_is_installed_with_its_state(monkeypatch):
    """Priority order, because that is the order they are actually tried in."""
    monkeypatch.setattr(
        ProvidersScreen, "_installed",
        staticmethod(lambda: [("anizone", 90, False), ("anikoto", 85, True)]),
    )
    app, _ = _make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(ProvidersScreen())
        await pilot.pause()
        items = list(app.screen.query(ProviderItem))
        assert [i.provider_name for i in items] == ["anizone", "anikoto"]
        assert [i.enabled for i in items] == [False, True]


async def test_turning_one_off_is_written_to_the_config(monkeypatch):
    written = {}
    monkeypatch.setattr(
        ProvidersScreen, "_installed",
        staticmethod(lambda: [("anizone", 90, True), ("anikoto", 85, True)]),
    )
    import anime_sh.config as config_mod
    monkeypatch.setattr(
        config_mod, "set_config_value",
        lambda k, v: written.update({k: v}), raising=False,
    )

    app, _ = _make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(ProvidersScreen())
        await pilot.pause()
        first = next(iter(app.screen.query(ProviderItem)))
        app.screen._toggle(first)
        await pilot.pause()

        assert first.enabled is False
        assert written == {"providers.disabled": "anizone"}


async def test_the_last_provider_cannot_be_turned_off(monkeypatch):
    """A configuration with every provider off cannot find a single episode.
    Refused here, as a choice, rather than surfacing later as "no sources"."""
    monkeypatch.setattr(
        ProvidersScreen, "_installed",
        staticmethod(lambda: [("anikoto", 85, True)]),
    )
    app, _ = _make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(ProvidersScreen())
        await pilot.pause()
        only = next(iter(app.screen.query(ProviderItem)))
        app.screen._toggle(only)
        await pilot.pause()
        assert only.enabled is True, "the last provider was switched off"


async def test_a_plugin_that_will_not_import_does_not_take_the_picker_down(
    monkeypatch,
):
    """The picker is where you would go to turn a broken plugin off, so it has
    to open when one is broken."""
    def boom():
        raise RuntimeError("entry point blew up")

    import anime_sh.infra.registry as registry
    monkeypatch.setattr(registry, "load_providers", boom)

    assert ProvidersScreen._installed() == []

    app, _ = _make_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.push_screen(ProvidersScreen())
        await pilot.pause()
        assert app.screen.query_one("#providers-list", ListView) is not None
