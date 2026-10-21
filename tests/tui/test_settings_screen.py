"""The unified Settings screen: one place, four categories, honest about overrides."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from anime_sh.app.settings import SettingsService
from anime_sh.config.loader import load_config
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.screens.settings import (
    SettingRow,
    SettingsScreen,
    ThemeRow,
    ToggleRow,
)

from scripts.tui_shots import _export_text

from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _noop


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in list(os.environ):
        if name.upper().startswith("ANIME_SH_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        SettingsScreen, "_installed_providers",
        staticmethod(lambda: [("anizone", 90), ("anikoto", 85)]),
    )
    monkeypatch.setattr(
        SettingsScreen, "_installed_resolvers",
        staticmethod(lambda: ["filemoon", "zoko"]),
    )


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


def _app(settings: SettingsService) -> AnimeShApp:
    services = TuiServices(
        search=FakeSearch(), metadata=FakeMetadata(), library=FakeLibrary(),
        playback=FakePlayback(), aclose=_noop, settings=settings,
    )
    return AnimeShApp(services, theme="midnight")


async def _settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def _rows(screen, cls=SettingRow):
    return list(screen.query(cls))


def _row(screen, key) -> SettingRow:
    for row in screen.query(SettingRow):
        if row.key == key:
            return row
    raise AssertionError(f"no row for {key}")


def _highlight(screen, item) -> None:
    from textual.widgets import ListView

    lv = screen.query_one("#settings-list", ListView)
    lv.index = list(lv.children).index(item)


async def test_comma_t_and_p_open_unified_settings_categories(cfg_path):
    app = _app(SettingsService(cfg_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        for key, category in (("comma", "appearance"), ("t", "appearance"),
                              ("p", "sources")):
            await pilot.press(key)
            await _settle(app, pilot)
            assert isinstance(app.screen, SettingsScreen), f"{key} opened {app.screen}"
            assert app.screen.category == category, key
            await pilot.press("escape")
            await _settle(app, pilot)
            assert not isinstance(app.screen, SettingsScreen)

        # Already in Settings: `p` moves to Sources instead of stacking a copy.
        await pilot.press("comma")
        await _settle(app, pilot)
        await pilot.press("p")
        await _settle(app, pilot)
        assert app.screen.category == "sources"
        assert sum(isinstance(s, SettingsScreen) for s in app.screen_stack) == 1


async def test_settings_tabs_change_with_left_and_right(cfg_path):
    app = _app(SettingsService(cfg_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await _settle(app, pilot)
        await pilot.press("comma")
        await _settle(app, pilot)
        screen = app.screen
        seen = []
        for _ in range(4):
            seen.append(screen.category)
            await pilot.press("right")
            await _settle(app, pilot)
        assert seen == ["appearance", "playback", "sources", "downloads"]
        assert screen.category == "appearance", "right from the last tab wraps"
        await pilot.press("left")
        await _settle(app, pilot)
        assert screen.category == "downloads"
        assert [r.key for r in _rows(screen)] == ["downloads.dir"]

        await pilot.press("left", "left")  # downloads -> sources -> playback
        await _settle(app, pilot)
        assert screen.category == "playback"
        shown = _export_text(app)
        for title in ("Appearance", "Playback", "Sources", "Downloads"):
            assert title in shown, f"{title} tab missing at 80 columns"
        keys = {r.key for r in _rows(screen)}
        assert {"player.name", "playback.quality", "playback.auto_next"} <= keys


async def test_override_fields_show_source_and_are_disabled(cfg_path, monkeypatch):
    monkeypatch.setenv("ANIME_SH_PLAYBACK__QUALITY", "720p")
    svc = SettingsService(cfg_path, cli_overrides={"player.name": "vlc"})
    app = _app(svc)
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        await pilot.press("comma")
        await _settle(app, pilot)
        await pilot.press("right")
        await _settle(app, pilot)
        screen = app.screen

        quality = _row(screen, "playback.quality")
        player = _row(screen, "player.name")
        assert quality.disabled and player.disabled
        assert "720p" in quality.text and "env" in quality.text
        assert "vlc" in player.text and "cli" in player.text
        assert not _row(screen, "playback.audio").disabled

        # Activating an overridden row does nothing to the file.
        await screen.activate(quality)
        await _settle(app, pilot)
        assert not cfg_path.exists()
        # The cursor never lands on a locked row.
        for _ in range(10):
            await pilot.press("down")
            await pilot.pause()
            assert not screen.query_one("#settings-list").highlighted_child.disabled


async def test_provider_toggle_keeps_one_enabled(cfg_path, monkeypatch):
    app = _app(SettingsService(cfg_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        await pilot.press("p")
        await _settle(app, pilot)
        screen = app.screen
        anizone, anikoto = [r for r in _rows(screen, ToggleRow) if r.group == "providers"]

        await screen.activate(anizone)
        await _settle(app, pilot)
        assert anizone.enabled is False
        assert load_config(cfg_path).providers.disabled == ["anizone"]

        before = cfg_path.read_bytes()
        await screen.activate(anikoto)
        await _settle(app, pilot)
        assert anikoto.enabled is True, "the last provider was switched off"
        assert cfg_path.read_bytes() == before
        assert "next launch" in _export_text(app)


async def test_resolver_toggle_persists_through_settings_service(cfg_path):
    svc = SettingsService(cfg_path)
    calls = []
    real = svc.update

    def spy(key, value):
        calls.append((key, value))
        return real(key, value)

    svc.update = spy  # type: ignore[method-assign]
    app = _app(svc)
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        await pilot.press("p")
        await _settle(app, pilot)
        screen = app.screen
        filemoon = next(r for r in _rows(screen, ToggleRow)
                        if r.group == "resolvers" and r.name_ == "filemoon")
        await screen.activate(filemoon)
        await _settle(app, pilot)

    assert calls == [("resolvers.disabled", "filemoon")]
    assert load_config(cfg_path).resolvers.disabled == ["filemoon"]


async def test_theme_preview_reverts_on_escape_and_saves_on_enter(cfg_path):
    app = _app(SettingsService(cfg_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        await pilot.press("t")
        await _settle(app, pilot)
        assert isinstance(app.screen.query_one("#settings-list").highlighted_child,
                          ThemeRow), "t should land on the theme list"
        await pilot.press("down")
        await pilot.pause()
        assert app.theme != "midnight", "moving the cursor did not preview"
        await pilot.press("escape")
        await _settle(app, pilot)
        assert app.theme == "midnight", "escape kept the previewed theme"
        assert not cfg_path.exists(), "browsing wrote the config"

        await pilot.press("t")
        await _settle(app, pilot)
        await pilot.press("down")
        await pilot.pause()
        chosen = app.theme
        await pilot.press("enter")
        await _settle(app, pilot)
        await pilot.press("escape")
        await _settle(app, pilot)
        assert app.theme == chosen
    assert load_config(cfg_path).ui.theme == chosen


async def test_settings_save_failure_keeps_last_valid_value(cfg_path, monkeypatch):
    import anime_sh.config.loader as loader

    cfg_path.write_text("[playback]\nauto_next = true\n", encoding="utf-8")
    before = cfg_path.read_bytes()
    app = _app(SettingsService(cfg_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        await pilot.press("comma")
        await _settle(app, pilot)
        await pilot.press("right")
        await _settle(app, pilot)
        screen = app.screen
        row = _row(screen, "playback.auto_next")

        def boom(src, dst):
            raise OSError("disk said no")

        real_replace = loader.os.replace
        monkeypatch.setattr(loader.os, "replace", boom)
        await screen.activate(row)
        await _settle(app, pilot)
        monkeypatch.setattr(loader.os, "replace", real_replace)
        detail = screen.query_one("#settings-detail")
        assert "disk said no" in screen.detail_text
        assert detail.has_class("-error")
        assert row.info.value is True and "on" in row.text

        # An invalid download folder is refused the same way.
        await pilot.press("right", "right")
        await _settle(app, pilot)
        dir_row = _row(screen, "downloads.dir")
        await screen.save_text(dir_row, "   ")
        await _settle(app, pilot)
        assert screen.query_one("#settings-detail").has_class("-error")
        assert dir_row.info.value == "~/Videos/anime"
    assert cfg_path.read_bytes() == before


async def test_download_dir_edit_is_saved_and_shown_expanded(cfg_path, tmp_path):
    app = _app(SettingsService(cfg_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        await pilot.press("comma")
        await _settle(app, pilot)
        await pilot.press("left")
        await _settle(app, pilot)
        screen = app.screen
        await pilot.press("enter")
        await _settle(app, pilot)
        box = screen.query_one("#settings-input")
        assert box.has_focus
        box.value = "~/anime-dl"
        await pilot.press("enter")
        await _settle(app, pilot)
        assert not box.has_focus
        assert str(Path("~/anime-dl").expanduser()) in screen.detail_text
    assert load_config(cfg_path).downloads.dir == "~/anime-dl"
    assert not Path("~/anime-dl").expanduser().exists(), "made the folder early"
