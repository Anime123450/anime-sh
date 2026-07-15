"""anime-sh — the terminal-native anime client.

Layered architecture: cli/tui → app (services) → domain (models + ports) with
infra/providers/resolvers as swappable adapters behind the ports. See
``docs/architecture.md``.
"""

__version__ = "0.1.0"

from .cli.main import main

__all__ = ["main", "__version__"]
