"""Configuration: schema, loading, precedence."""

from .schema import Config
from .loader import load_config, config_path

__all__ = ["Config", "load_config", "config_path"]
