"""Load and persist config from TOML with env overrides."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, get_args, get_origin

from ..domain.errors import ConfigError
from .paths import config_dir
from .schema import Config


def config_path() -> Path:
    return config_dir() / "config.toml"


def _read_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        # utf-8-sig, not utf-8: Notepad writes a BOM by default on Windows, and a
        # BOM made the whole config unreadable ("could not read config") for
        # anyone who edited the file with it.
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"could not read config at {path}: {e}") from e


def load_config(path: Path | None = None) -> Config:
    """Merge TOML file (if present) with env vars and defaults.

    File values seed the model; ``ANIME_SH_*`` env vars override them because
    pydantic-settings applies env on top of explicitly-passed values.
    """
    path = path or config_path()
    file_values = _read_raw(path)
    try:
        return Config(**file_values)
    except Exception as e:  # pydantic ValidationError et al.
        raise ConfigError(f"invalid config at {path}: {e}") from e


def set_config_value(dotted_key: str, value: str, path: Path | None = None) -> Any:
    """Set ``section.field`` in the config file, validating before writing.

    The value is coerced to the field's declared type, the whole config is
    re-validated, and only then is the file rewritten. Returns the typed value.
    """
    path = path or config_path()
    section, _, field = dotted_key.partition(".")
    if not field or "." in field:
        raise ConfigError(f"key must be 'section.field' (got {dotted_key!r})")
    if section not in Config.model_fields:
        raise ConfigError(f"no such config section: {section!r}")
    sub_model = Config.model_fields[section].annotation
    if field not in getattr(sub_model, "model_fields", {}):
        raise ConfigError(f"no such setting: {section}.{field}")

    typed = _coerce(value, sub_model.model_fields[field].annotation)
    _reject_unknown_choice(dotted_key, typed)
    raw = _read_raw(path)
    raw.setdefault(section, {})[field] = typed
    try:
        Config(**raw)  # validate the merged result before persisting
    except Exception as e:
        raise ConfigError(f"{dotted_key}={value!r} is invalid: {e}") from e

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_toml(raw), encoding="utf-8")
    return typed


# Settings typed as plain strings that nonetheless accept only a fixed set.
# They stay `str` in the schema so an old config file with an odd value still
# loads (playback falls back to a sane default) — but writing one is a typo, and
# a typo that silently changes behaviour is worse than an error.
_CHOICES: dict[str, tuple[str, ...]] = {
    "playback.quality": ("best", "1080p", "720p", "480p", "360p", "worst"),
    "playback.audio": ("sub", "dub"),
}


def _reject_unknown_choice(dotted_key: str, typed: Any) -> None:
    allowed = _CHOICES.get(dotted_key)
    if allowed and str(typed) not in allowed:
        raise ConfigError(
            f"{dotted_key} must be one of: {', '.join(allowed)} (got {typed!r})"
        )


def _coerce(value: str, annotation: Any) -> Any:
    """Coerce a CLI string to a config field's type."""
    origin = get_origin(annotation)
    if origin in (list, tuple) or annotation in (list, tuple):
        return [v.strip() for v in value.split(",") if v.strip()]
    # Unwrap Optional[...] to the concrete type.
    if origin is not None:
        args = [a for a in get_args(annotation) if a is not type(None)]
        annotation = args[0] if args else str
    if annotation is bool:
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ConfigError(f"expected true/false, got {value!r}")
    if annotation is int:
        try:
            return int(value)
        except ValueError:
            raise ConfigError(f"expected an integer, got {value!r}") from None
    if annotation is float:
        try:
            return float(value)
        except ValueError:
            raise ConfigError(f"expected a number, got {value!r}") from None
    return value


def _dump_toml(data: dict[str, Any]) -> str:
    """Minimal TOML writer for the flat ``[section] key = value`` config shape."""
    lines: list[str] = []
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for key, val in values.items():
            lines.append(f"{key} = {_fmt(val)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _fmt(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return repr(val)
    if isinstance(val, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in val) + "]"
    escaped = str(val).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
