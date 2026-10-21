"""Typed settings: what each setting is, where its value came from, and writes.

The Settings screen needs more than ``load_config()`` gives it. A value set by a
CLI flag or an ``ANIME_SH_*`` variable wins over the file for this run, so
letting someone "change" it would write the file and change nothing they can
see. This service reports each effective value together with its source, keeps
overridden fields read-only, and routes every write through the one validated,
atomic config setter so a widget can never hand-edit TOML.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from ..config.loader import _read_raw, config_path, load_config, set_config_value
from ..config.schema import Config
from ..domain.errors import ConfigError

Source = Literal["cli", "env", "file", "default"]


@dataclass(frozen=True, slots=True)
class SettingInfo:
    key: str
    value: Any
    source: Source
    read_only: bool


def _split(dotted_key: str) -> tuple[str, str]:
    section, _, field = dotted_key.partition(".")
    if not field or "." in field:
        raise ConfigError(f"key must be 'section.field' (got {dotted_key!r})")
    if section not in Config.model_fields:
        raise ConfigError(f"no such config section: {section!r}")
    sub_model = Config.model_fields[section].annotation
    if field not in getattr(sub_model, "model_fields", {}):
        raise ConfigError(f"no such setting: {section}.{field}")
    return section, field


def env_name(dotted_key: str) -> str:
    """The environment variable that overrides ``section.field``."""
    section, field = _split(dotted_key)
    return f"ANIME_SH_{section}__{field}".upper()


class SettingsService:
    """Reads and writes settings for one run.

    ``cli_overrides`` maps ``section.field`` to the value a command-line flag
    forced for this run. Those values, and any set through the environment, are
    reported with their source and cannot be updated here.
    """

    def __init__(
        self,
        config_path: Path | None = None,
        cli_overrides: Mapping[str, Any] | None = None,
    ) -> None:
        self._path = config_path
        self._cli: dict[str, Any] = {}
        for key, value in (cli_overrides or {}).items():
            _split(key)
            self._cli[key] = value

    @property
    def path(self) -> Path:
        return self._path or config_path()

    # -- reads ---------------------------------------------------------------- #
    def snapshot(self) -> Config:
        """The validated effective config: defaults < file < env < CLI."""
        cfg = load_config(self.path)
        if not self._cli:
            return cfg
        data = cfg.model_dump()
        for key, value in self._cli.items():
            section, field = key.split(".", 1)
            data[section][field] = value
        sections = {
            name: Config.model_fields[name].annotation(**values)
            for name, values in data.items()
        }
        # model_construct, not Config(...): Config is a BaseSettings and would
        # consult the environment again, re-applying env over the CLI value.
        return Config.model_construct(**sections)

    def source(self, dotted_key: str) -> Source:
        section, field = _split(dotted_key)
        if dotted_key in self._cli:
            return "cli"
        if self._env_set(dotted_key):
            return "env"
        raw = _read_raw(self.path)
        if isinstance(raw.get(section), dict) and field in raw[section]:
            return "file"
        return "default"

    def inspect(self, dotted_key: str) -> SettingInfo:
        section, field = _split(dotted_key)
        source = self.source(dotted_key)
        value = getattr(getattr(self.snapshot(), section), field)
        return SettingInfo(
            key=dotted_key,
            value=value,
            source=source,
            read_only=source in ("cli", "env"),
        )

    # -- writes --------------------------------------------------------------- #
    def update(self, dotted_key: str, value: str) -> SettingInfo:
        """Validate and persist one setting, then report its new state.

        Refuses a field a flag or env var controls: the file would change while
        the run kept using the override, which looks exactly like a broken
        setting. The file is untouched on any refusal or validation failure.
        """
        source = self.source(dotted_key)
        if source in ("cli", "env"):
            how = (
                "a command-line flag"
                if source == "cli"
                else f"the {env_name(dotted_key)} environment variable"
            )
            raise ConfigError(
                f"{dotted_key} is set by {how} ({source}) for this run; "
                "change it there instead"
            )
        set_config_value(dotted_key, value, self.path)
        return self.inspect(dotted_key)

    @staticmethod
    def _env_set(dotted_key: str) -> bool:
        want = env_name(dotted_key)
        # Windows env names are case-insensitive; pydantic-settings matches
        # them case-insensitively everywhere, so do the same.
        return any(name.upper() == want for name in os.environ)
