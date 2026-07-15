"""Load and persist config from TOML with env overrides."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ..domain.errors import ConfigError
from .paths import config_dir
from .schema import Config


def config_path() -> Path:
    return config_dir() / "config.toml"


def load_config(path: Path | None = None) -> Config:
    """Merge TOML file (if present) with env vars and defaults.

    File values seed the model; ``ANIME_SH_*`` env vars override them because
    pydantic-settings applies env on top of explicitly-passed values.
    """
    path = path or config_path()
    file_values: dict[str, Any] = {}
    if path.exists():
        try:
            file_values = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise ConfigError(f"could not read config at {path}: {e}") from e
    try:
        return Config(**file_values)
    except Exception as e:  # pydantic ValidationError et al.
        raise ConfigError(f"invalid config at {path}: {e}") from e
