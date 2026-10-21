"""SettingsService — effective values, where they come from, and safe writes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from anime_sh.config.loader import load_config, set_config_value
from anime_sh.domain.errors import ConfigError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # A developer's own ANIME_SH_* variables must not leak into source checks.
    for name in list(os.environ):
        if name.upper().startswith("ANIME_SH_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


def test_player_name_defaults_to_mpv_and_rejects_unknown(cfg_path):
    cfg = load_config(cfg_path)
    assert cfg.player.name == "mpv"
    assert cfg.player.vlc_path == ""

    assert set_config_value("player.name", "vlc", cfg_path) == "vlc"
    with pytest.raises(ConfigError):
        set_config_value("player.name", "vscode", cfg_path)
    assert load_config(cfg_path).player.name == "vlc"

    cfg_path.write_text('[player]\nname = "quicktime"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_setting_origin_follows_cli_env_file_default_precedence(cfg_path, monkeypatch):
    from anime_sh.app.settings import SettingsService

    cfg_path.write_text(
        '[playback]\nquality = "720p"\n\n[ui]\ntheme = "nord"\n', encoding="utf-8"
    )
    monkeypatch.setenv("ANIME_SH_PLAYBACK__QUALITY", "480p")
    monkeypatch.setenv("ANIME_SH_PLAYBACK__AUDIO", "dub")
    svc = SettingsService(cfg_path, cli_overrides={"playback.quality": "1080p"})

    quality = svc.inspect("playback.quality")
    assert (quality.value, quality.source, quality.read_only) == ("1080p", "cli", True)
    audio = svc.inspect("playback.audio")
    assert (audio.value, audio.source, audio.read_only) == ("dub", "env", True)
    theme = svc.inspect("ui.theme")
    assert (theme.value, theme.source, theme.read_only) == ("nord", "file", False)
    auto_next = svc.inspect("playback.auto_next")
    assert (auto_next.value, auto_next.source, auto_next.read_only) == (True, "default", False)

    snap = svc.snapshot()
    assert snap.playback.quality == "1080p"
    assert snap.playback.audio == "dub"
    assert snap.ui.theme == "nord"

    with pytest.raises(ConfigError):
        svc.inspect("playback.nope")


def test_overrides_are_visible_read_only_and_leave_toml_unchanged(cfg_path, monkeypatch):
    from anime_sh.app.settings import SettingsService

    cfg_path.write_text('[playback]\nquality = "720p"\naudio = "sub"\n', encoding="utf-8")
    before = cfg_path.read_bytes()
    monkeypatch.setenv("ANIME_SH_PLAYBACK__AUDIO", "dub")
    svc = SettingsService(cfg_path, cli_overrides={"playback.quality": "1080p"})

    with pytest.raises(ConfigError, match="cli"):
        svc.update("playback.quality", "480p")
    with pytest.raises(ConfigError, match="env"):
        svc.update("playback.audio", "sub")
    assert cfg_path.read_bytes() == before

    # A field nothing overrides is still editable, and the write reports back.
    info = svc.update("playback.auto_next", "false")
    assert (info.value, info.source, info.read_only) == (False, "file", False)


def test_invalid_update_preserves_original_toml(cfg_path):
    from anime_sh.app.settings import SettingsService

    cfg_path.write_text('[providers]\nparallel = 3\n', encoding="utf-8")
    before = cfg_path.read_bytes()
    svc = SettingsService(cfg_path)

    for key, bad in [
        ("playback.quality", "nonsense"),
        ("providers.parallel", "0"),
        ("providers.parallel", "lots"),
        ("player.name", "vscode"),
    ]:
        with pytest.raises(ConfigError):
            svc.update(key, bad)
        assert cfg_path.read_bytes() == before, key

    assert svc.snapshot().providers.parallel == 3


def test_replace_failure_preserves_original_toml(cfg_path, monkeypatch):
    import anime_sh.config.loader as loader
    from anime_sh.app.settings import SettingsService

    cfg_path.write_text('[ui]\ntheme = "nord"\n', encoding="utf-8")
    before = cfg_path.read_bytes()

    def boom(src, dst):
        raise OSError("disk said no")

    monkeypatch.setattr(loader.os, "replace", boom)
    with pytest.raises(ConfigError):
        SettingsService(cfg_path).update("ui.theme", "dracula")

    assert cfg_path.read_bytes() == before
    assert sorted(p.name for p in cfg_path.parent.iterdir()) == ["config.toml"]
