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


def test_set_rejects_a_value_outside_the_allowed_set(tmp_path):
    """`config set` is documented as validated, but quality/audio are plain
    strings in the schema — so a typo saved happily and then silently played at
    the wrong quality (an unknown target falls back to 1080p)."""
    from anime_sh.config.loader import ConfigError, set_config_value

    path = tmp_path / "config.toml"
    with pytest.raises(ConfigError, match="must be one of"):
        set_config_value("playback.quality", "nonsense", path)
    with pytest.raises(ConfigError, match="must be one of"):
        set_config_value("playback.audio", "spanish", path)
    # Valid values still go through.
    assert set_config_value("playback.quality", "720p", path) == "720p"
    assert set_config_value("playback.audio", "dub", path) == "dub"


def test_a_utf8_bom_does_not_break_the_config(tmp_path):
    """Notepad writes a BOM by default on Windows, and reading strict utf-8 made
    the whole config unreadable for anyone who edited it there."""
    from anime_sh.config.loader import load_config

    path = tmp_path / "config.toml"
    path.write_text('[ui]\ntheme = "tokyo-night"\n', encoding="utf-8-sig")
    assert load_config(path).ui.theme == "tokyo-night"


def test_provider_parallelism_must_be_at_least_one(tmp_path):
    """`providers[:parallel]` with a negative value slices the list down to
    nothing, so every playback attempt silently found no sources at all."""
    from anime_sh.config.loader import ConfigError, load_config

    path = tmp_path / "config.toml"
    path.write_text("[providers]\nparallel = -3\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)

    path.write_text("[providers]\nparallel = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)

    path.write_text("[providers]\nparallel = 2\n", encoding="utf-8")
    assert load_config(path).providers.parallel == 2
