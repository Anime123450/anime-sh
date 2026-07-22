"""Platform-correct application directories.

Uses XDG on Linux/macOS and the native equivalents on Windows via platformdirs,
so anime-sh drops files in the right place on every OS without special-casing.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import PlatformDirs

_dirs = PlatformDirs(appname="anime-sh", appauthor=False, roaming=False)


def config_dir() -> Path:
    return Path(_dirs.user_config_dir)


def data_dir() -> Path:
    return Path(_dirs.user_data_dir)


def cache_dir() -> Path:
    return Path(_dirs.user_cache_dir)


def user_db_path() -> Path:
    """The sacred store — user progress/favorites/history."""
    return data_dir() / "anime.db"


def cache_db_path() -> Path:
    """The disposable store — metadata/search/candidate caches."""
    return cache_dir() / "cache.db"


def anilist_token_path() -> Path:
    """Where the AniList OAuth token is cached. No OS keyring is bundled, so
    this file is the store; it is created 0600 and holds only the token, never
    the user's password."""
    return config_dir() / "anilist_token.json"
