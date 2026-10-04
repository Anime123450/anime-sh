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


def covers_dir() -> Path:
    """Cached cover art, one file per image URL.

    Under the cache directory because every byte of it is re-fetchable, so
    `anime cache clear` is free to take the lot. Lives here rather than beside
    the renderer so that `anime cache info` can weigh it without importing the
    TUI — Textual is an optional extra, and a cache command that needs it is a
    cache command that crashes on a plain install.
    """
    return cache_dir() / "covers"


def covers_on_disk() -> tuple[int, int]:
    """``(files, bytes)`` of cached cover art. ``(0, 0)`` if there is none."""
    try:
        files = [p for p in covers_dir().iterdir() if p.is_file()]
    except OSError:
        return (0, 0)
    # ponytail: no size cap. A cover is ~40KB and a heavy library a few MB; add
    # an LRU sweep here if `cache info` ever reports something alarming.
    return (len(files), sum(p.stat().st_size for p in files))


def clear_covers() -> int:
    """Delete every cached cover. Returns how many files went."""
    gone = 0
    try:
        entries = list(covers_dir().iterdir())
    except OSError:
        return 0
    for f in entries:
        try:
            f.unlink()
            gone += 1
        except OSError:
            pass
    return gone


def anilist_token_path() -> Path:
    """Where the AniList OAuth token is cached. No OS keyring is bundled, so
    this file is the store; it is created 0600 and holds only the token, never
    the user's password."""
    return config_dir() / "anilist_token.json"
