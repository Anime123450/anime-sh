"""anime-sh — the terminal-native anime client.

Layered architecture: cli/tui → app (services) → domain (models + ports) with
infra/providers/resolvers as swappable adapters behind the ports. See
``docs/architecture.md``.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    # Single source of truth: the version declared in pyproject/installed dist.
    __version__ = _pkg_version("anime-sh")
except PackageNotFoundError:  # running from a raw checkout that isn't installed
    __version__ = "0.0.0+unknown"

from .cli.main import main

__all__ = ["main", "__version__"]
