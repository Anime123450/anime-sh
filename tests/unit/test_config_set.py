"""`config set` — coercion, validation, and TOML round-trip."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from anime_sh.config.loader import _dump_toml, load_config, set_config_value
from anime_sh.domain.errors import ConfigError


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


def test_set_coerces_scalar_bool_int_and_list(cfg_path):
    assert set_config_value("playback.quality", "1080p", cfg_path) == "1080p"
    assert set_config_value("playback.auto_next", "false", cfg_path) is False
    assert set_config_value("providers.parallel", "3", cfg_path) == 3
    assert set_config_value("providers.disabled", "allanime, anikoto", cfg_path) == [
        "allanime",
        "anikoto",
    ]
    cfg = load_config(cfg_path)
    assert cfg.playback.quality == "1080p" and cfg.playback.auto_next is False
    assert cfg.providers.parallel == 3
    assert cfg.providers.disabled == ["allanime", "anikoto"]


def test_written_file_is_valid_toml(cfg_path):
    set_config_value("ui.theme", "nord", cfg_path)
    set_config_value("playback.audio", "dub", cfg_path)
    parsed = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    assert parsed["ui"]["theme"] == "nord"
    assert parsed["playback"]["audio"] == "dub"


def test_rejects_unknown_and_malformed(cfg_path):
    with pytest.raises(ConfigError):
        set_config_value("nope.field", "x", cfg_path)
    with pytest.raises(ConfigError):
        set_config_value("playback.quality", "x", cfg_path)  # missing .field
        set_config_value("playback", "x", cfg_path)
    with pytest.raises(ConfigError):
        set_config_value("playback.auto_next", "maybe", cfg_path)  # not a bool
    with pytest.raises(ConfigError):
        set_config_value("providers.parallel", "lots", cfg_path)  # not an int


def test_set_preserves_other_values(cfg_path):
    set_config_value("playback.quality", "720p", cfg_path)
    set_config_value("ui.theme", "dracula", cfg_path)
    cfg = load_config(cfg_path)
    assert cfg.playback.quality == "720p" and cfg.ui.theme == "dracula"


def test_dump_toml_formats_types():
    out = _dump_toml({"s": {"b": True, "n": 3, "t": "x", "l": ["a", "b"]}})
    parsed = tomllib.loads(out)
    assert parsed["s"] == {"b": True, "n": 3, "t": "x", "l": ["a", "b"]}
