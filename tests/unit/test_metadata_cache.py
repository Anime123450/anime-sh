"""AniList metadata caching — a wired KvCache serves repeat queries offline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from anime_sh.domain.models import AnimeId, Season
from anime_sh.infra.cache.kv import KvCache
from anime_sh.infra.db.database import Database
from anime_sh.infra.metadata.anilist import AniListMetadata


class _CountingHttp:
    """Records how many POSTs it served and replays a canned response."""

    def __init__(self, response):
        self.calls = 0
        self._response = response

    async def post_json(self, url, *, json=None, headers=None):
        self.calls += 1
        return self._response


@pytest.fixture
async def cache(tmp_path: Path):
    db = Database(tmp_path / "cache.db", migrations_dir="migrations_cache")
    await db.connect()
    yield KvCache(db)
    await db.close()


_PAGE = {"data": {"Page": {"media": [{"id": 1, "title": {"romaji": "A"}}]}}}
_MEDIA = {"data": {"Media": {"id": 7, "title": {"romaji": "X"}}}}
_SCHED = {"data": {"Page": {"airingSchedules": [
    {"episode": 3, "airingAt": 1_800_000_000, "media": {"id": 5, "title": {"romaji": "S"}}}
]}}}


async def test_search_second_call_served_from_cache(cache):
    http = _CountingHttp(_PAGE)
    meta = AniListMetadata(http=http, cache=cache)
    a = await meta.search("frieren")
    b = await meta.search("frieren")
    assert http.calls == 1  # second call never hit the network
    assert a[0].id.anilist == b[0].id.anilist == 1


async def test_distinct_queries_are_cached_separately(cache):
    http = _CountingHttp(_PAGE)
    meta = AniListMetadata(http=http, cache=cache)
    await meta.search("frieren")
    await meta.search("bleach")
    assert http.calls == 2


async def test_get_and_trending_and_seasonal_cache(cache):
    meta = AniListMetadata(http=_CountingHttp(_MEDIA), cache=cache)
    await meta.get(AnimeId(anilist=7))
    assert meta._http.calls == 1
    await meta.get(AnimeId(anilist=7))
    assert meta._http.calls == 1

    meta2 = AniListMetadata(http=_CountingHttp(_PAGE), cache=cache)
    await meta2.trending(limit=30)
    await meta2.trending(limit=30)
    assert meta2._http.calls == 1


async def test_schedule_cached(cache):
    http = _CountingHttp(_SCHED)
    meta = AniListMetadata(http=http, cache=cache)
    d0, d1 = date(2027, 1, 1), date(2027, 1, 8)
    await meta.airing_schedule(d0, d1)
    ev = await meta.airing_schedule(d0, d1)
    assert http.calls == 1
    assert ev and ev[0].episode == 3.0


async def test_no_cache_means_every_call_hits_network():
    http = _CountingHttp(_PAGE)
    meta = AniListMetadata(http=http)  # no cache wired
    await meta.search("x")
    await meta.search("x")
    assert http.calls == 2


async def test_clear_forces_a_refetch(cache):
    http = _CountingHttp(_PAGE)
    meta = AniListMetadata(http=http, cache=cache)
    await meta.search("frieren")
    removed = await cache.clear()
    assert removed >= 1
    await meta.search("frieren")
    assert http.calls == 2


async def test_expired_entries_are_swept_without_a_maintenance_command(tmp_path):
    """A cache keyed by search query is mostly keys nobody types twice.

    Expired rows were only dropped when that exact key was read again, so they
    accumulated forever — a real install was found at 55 rows of which 53 were
    already expired, in a 2.3 MB file. Nothing called purge_expired except the
    manual `anime cache purge` command.
    """
    from datetime import timedelta

    from anime_sh.infra.cache.kv import _SWEEP_EVERY_WRITES, KvCache
    from anime_sh.infra.db.database import Database

    db = Database(tmp_path / "cache.db", migrations_dir="migrations_cache")
    conn = await db.connect()
    cache = KvCache(db)
    try:
        # Write a pile of entries that are already dead on arrival.
        for i in range(_SWEEP_EVERY_WRITES - 1):
            await cache.set(f"stale-{i}", {"i": i}, ttl=timedelta(seconds=-1))
        rows = (await (await conn.execute("SELECT COUNT(*) FROM kv_cache")).fetchone())[0]
        assert rows == _SWEEP_EVERY_WRITES - 1, "nothing should have swept yet"

        # The write that reaches the cadence sweeps the dead ones away.
        await cache.set("fresh", {"ok": True}, ttl=timedelta(hours=1))
        rows = (await (await conn.execute("SELECT COUNT(*) FROM kv_cache")).fetchone())[0]
        assert rows == 1, f"expired entries were not swept (kept {rows})"
        assert await cache.get("fresh") == {"ok": True}
    finally:
        await db.close()
