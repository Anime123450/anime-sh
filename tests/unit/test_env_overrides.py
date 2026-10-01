"""``ANIME_SH_*`` must outrank the config file.

The documented order is CLI flag > env > config file > defaults. The file is
handed to `Config` as init kwargs, and pydantic-settings ranks init *above* env
by default -- so every variable was silently ignored for any setting the file
also mentioned, which is most of them once `config set` has written one. Silently
is the part that stings: no error, no warning, the variable simply did nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anime_sh.config.loader import load_config, set_config_value
from anime_sh.domain.errors import ConfigError


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        '[ui]\ntheme = "midnight"\nepisodes = "list"\n\n'
        "[providers]\nparallel = 5\ntimeout_s = 8.0\n",
        encoding="utf-8",
    )
    return path


def test_env_beats_a_value_the_file_also_sets(cfg, monkeypatch):
    assert load_config(cfg).ui.theme == "midnight"
    monkeypatch.setenv("ANIME_SH_UI__THEME", "nord")
    assert load_config(cfg).ui.theme == "nord"


def test_env_beats_the_file_for_non_string_settings(cfg, monkeypatch):
    """Coercion still has to happen on the way through, so a number set by env
    is a number and not the string the environment actually carries."""
    monkeypatch.setenv("ANIME_SH_PROVIDERS__PARALLEL", "9")
    monkeypatch.setenv("ANIME_SH_PROVIDERS__TIMEOUT_S", "30")
    providers = load_config(cfg).providers
    assert providers.parallel == 9
    assert providers.timeout_s == 30.0


def test_overriding_one_setting_leaves_its_neighbours_alone(cfg, monkeypatch):
    """Sources are merged per field, not per section. If they were not, setting
    `ui.theme` from the environment would drop `ui.episodes` from the file and
    silently return the default layout."""
    monkeypatch.setenv("ANIME_SH_UI__THEME", "nord")
    ui = load_config(cfg).ui
    assert ui.theme == "nord"
    assert ui.episodes == "list"


def test_the_file_still_beats_the_defaults(cfg):
    """The reorder must not go too far the other way."""
    ui = load_config(cfg).ui
    assert ui.theme == "midnight"  # default is also "midnight"...
    assert ui.episodes == "list"  # ...this one is not: the default is "grid"


def test_env_still_applies_with_no_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIME_SH_UI__THEME", "nord")
    assert load_config(tmp_path / "absent.toml").ui.theme == "nord"


def test_config_set_validates_the_file_and_not_the_environment(cfg, monkeypatch):
    """`config set` validated the merged `Config`, which reads the environment.

    With a matching variable set, the value that got validated was the one from
    the environment while the bad one was what reached the file -- so the command
    reported success and left behind a config that refuses to load.
    """
    monkeypatch.setenv("ANIME_SH_UI__THEME", "nord")
    with pytest.raises(ConfigError, match="drakula"):
        set_config_value("ui.theme", "drakula", cfg)
    assert "drakula" not in cfg.read_text(encoding="utf-8")

    # ...and a good value still writes, with the env var still set.
    assert set_config_value("ui.theme", "gruvbox", cfg) == "gruvbox"
    assert 'theme = "gruvbox"' in cfg.read_text(encoding="utf-8")
