"""Covers survive a restart, and a failed fetch does not poison the cache.

Twelve posters on a shelf is twelve HTTPS round trips per launch without this,
and they are the same twelve every time. The thing worth guarding is less the
hit than the *miss*: an entry written half-way, or written at all for a 404, is
a cover that never renders again and never retries either.
"""

from __future__ import annotations

import pytest

from anime_sh.tui import coverart


@pytest.fixture
def covers(tmp_path, monkeypatch):
    """Point the cache at a temp dir. Never the real one — this writes."""
    d = tmp_path / "covers"
    monkeypatch.setattr("anime_sh.config.paths.covers_dir", lambda: d)
    return d


async def test_a_fetched_cover_is_served_from_disk_next_time(covers, monkeypatch):
    calls = []

    class _Resp:
        status_code = 200
        content = b"\x89PNG-pretend"

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            calls.append(url)
            return _Resp()

    monkeypatch.setitem(__import__("sys").modules, "httpx",
                        type("m", (), {"AsyncClient": _Client}))

    url = "https://example.invalid/cover.jpg?x=1"
    assert await coverart.fetch_cover(url) == b"\x89PNG-pretend"
    assert calls == [url], "first call should hit the network"
    assert await coverart.fetch_cover(url) == b"\x89PNG-pretend"
    assert calls == [url], "second call went to the network instead of disk"


async def test_a_failed_fetch_caches_nothing(covers, monkeypatch):
    class _Resp:
        status_code = 404
        content = b""

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()

    monkeypatch.setitem(__import__("sys").modules, "httpx",
                        type("m", (), {"AsyncClient": _Client}))

    url = "https://example.invalid/missing.jpg"
    assert await coverart.fetch_cover(url) is None
    assert coverart.cached_cover(url) is None, (
        "a 404 was written to the cache, so the cover can never be retried"
    )


def test_a_new_url_is_a_new_entry(covers):
    """Keyed on the URL, not the show.

    AniList replaces cover images in place on the show and gives the new one a
    new URL. Keying on the anilist id would serve the old picture forever out of
    a cache entry that looks perfectly valid.
    """
    coverart._store_cover("https://example.invalid/a.jpg", b"old")
    coverart._store_cover("https://example.invalid/b.jpg", b"new")
    assert coverart.cached_cover("https://example.invalid/a.jpg") == b"old"
    assert coverart.cached_cover("https://example.invalid/b.jpg") == b"new"


def test_clearing_takes_the_covers(covers):
    from anime_sh.config.paths import clear_covers, covers_on_disk

    coverart._store_cover("https://example.invalid/a.jpg", b"xxxx")
    coverart._store_cover("https://example.invalid/b.jpg", b"yy")
    assert covers_on_disk() == (2, 6)
    assert clear_covers() == 2
    assert covers_on_disk() == (0, 0)


def test_no_covers_directory_is_not_an_error(tmp_path, monkeypatch):
    """`cache info` runs on a fresh install, where nothing has been fetched."""
    from anime_sh.config import paths

    monkeypatch.setattr(paths, "covers_dir", lambda: tmp_path / "nope")
    assert paths.covers_on_disk() == (0, 0)
    assert paths.clear_covers() == 0
