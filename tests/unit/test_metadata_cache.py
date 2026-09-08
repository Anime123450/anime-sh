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


async def test_an_expired_entry_survives_the_read_that_finds_it_expired(cache):
    """`get` used to delete the row when it noticed the TTL had passed.

    That threw the last known value away at precisely the moment it becomes
    valuable: the read that discovers an entry is stale is usually the read that
    is about to go and ask the upstream, and if the upstream is down that stale
    copy is the only thing left. Sweeping still happens — on a write cadence and
    via `purge_expired` — so the file stays bounded either way.
    """
    from datetime import timedelta

    await cache.set("trending:30", [{"id": 1}], ttl=timedelta(seconds=-1))
    assert await cache.get("trending:30") is None, "an expired entry was served"
    total, expired = await cache.stats()
    assert (total, expired) == (1, 1), "the read destroyed the stale copy"


async def test_a_stale_entry_is_served_while_it_is_still_worth_something(cache):
    """Bounded on purpose: past a point "stale" and "wrong" are the same thing.
    A week-old trending list is still roughly trending; a month-old one is a lie
    with a timestamp on it."""
    from datetime import timedelta

    await cache.set("recent", ["a"], ttl=timedelta(days=-1))    # expired yesterday
    await cache.set("ancient", ["b"], ttl=timedelta(days=-40))  # expired long ago

    assert await cache.get_stale("recent", max_age=timedelta(days=7)) == ["a"]
    assert await cache.get_stale("ancient", max_age=timedelta(days=7)) is None
    assert await cache.get_stale("missing", max_age=timedelta(days=7)) is None


async def test_the_last_known_answer_is_served_when_the_upstream_is_down(cache):
    """The day AniList disabled its own public API, every browse section on the
    home screen went blank. Nothing was wrong locally — the cache simply had no
    way to say "I still have yesterday's".
    """
    from datetime import timedelta

    await cache.set("k", ["yesterday"], ttl=timedelta(hours=-1))
    meta = AniListMetadata(cache=cache)

    async def down():
        raise RuntimeError("The AniList API has been temporarily disabled")

    assert await meta._cached("k", timedelta(hours=1), down) == ["yesterday"]

    # With nothing cached at all the failure is still a failure — serving
    # nothing quietly would be worse than saying the upstream is down.
    with pytest.raises(RuntimeError):
        await meta._cached("never-seen", timedelta(hours=1), down)
