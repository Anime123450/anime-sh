"""Configuration: schema, loading, precedence."""

from .schema import Config
from .loader import config_path, load_config, set_config_value

__all__ = ["Config", "load_config", "config_path", "set_config_value"]
