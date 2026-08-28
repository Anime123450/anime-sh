"""The cache commands say what they will do before they do it.

`cache clear` (wipe everything) and `cache purge` (drop stale entries only) are
synonyms in English, and nothing in either name said which one threw away data
you were still using. Swapping their meanings would silently change what an
existing `cache clear` in someone's script does, so instead the safe operation
got an unambiguous name (`prune`), the destructive one asks, and `cache info`
exists so the answer is informed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from anime_sh.infra.cache.kv import KvCache
from anime_sh.infra.db.database import Database


@pytest.fixture
async def cache(tmp_path):
    db = Database(tmp_path / "cache.db", migrations_dir="migrations_cache")
    kv = KvCache(db)
    yield kv
    await db.close()


async def test_stats_separates_stale_entries_from_current_ones(cache):
    """`cache clear` needs to be able to say "900 of these are still current",
    which is the number that makes it a decision rather than a guess."""
    await cache.set("fresh-1", {"a": 1}, ttl=timedelta(hours=1))
    await cache.set("fresh-2", {"a": 2}, ttl=timedelta(hours=1))
    await cache.set("stale", {"a": 3}, ttl=timedelta(seconds=-1))

    total, expired = await cache.stats()
    assert total == 3
    assert expired == 1


async def test_stats_on_an_empty_cache_is_zero_not_an_error(cache):
    assert await cache.stats() == (0, 0)


async def test_pruning_keeps_what_is_still_current(cache):
    """The whole point of the rename: this one is safe."""
    await cache.set("fresh", {"a": 1}, ttl=timedelta(hours=1))
    await cache.set("stale", {"a": 2}, ttl=timedelta(seconds=-1))

    assert await cache.purge_expired() == 1
    assert await cache.stats() == (1, 0)
    assert await cache.get("fresh") == {"a": 1}


async def test_clearing_reclaims_the_disk_it_freed(cache, tmp_path):
    """DELETE leaves freed pages inside the SQLite file, so an emptied 1.5 MB
    cache stayed 1.5 MB. Reclaiming disk is the usual reason to run this, and
    "cleared" beside an unchanged file size reads like it did not work.
    """
    payload = {"blob": "x" * 20_000}
    for i in range(60):
        await cache.set(f"k{i}", payload, ttl=timedelta(hours=1))

    # Sum the whole database, not just cache.db: in WAL mode the rows live in
    # cache.db-wal until a checkpoint, so the main file alone reads 4 KB and
    # would make this test measure nothing.
    def on_disk() -> int:
        return sum(f.stat().st_size for f in tmp_path.glob("cache.db*"))

    grew_to = on_disk()
    assert grew_to > 400_000, f"fixture too small to measure: {grew_to} bytes"

    assert await cache.clear() == 60
    assert await cache.stats() == (0, 0)
    assert on_disk() < grew_to / 2, (
        f"database stayed at {on_disk()} of {grew_to} bytes — "
        f"the freed pages were not returned to the filesystem"
    )


async def test_a_failing_vacuum_does_not_fail_the_clear(cache, monkeypatch):
    """VACUUM is housekeeping. If it cannot run — the file is locked, or there is
    no room for the temporary copy — the entries are still gone and the command
    has still succeeded."""
    real_execute = (await cache._db.connect()).execute

    async def explode_on_vacuum(sql, *a, **kw):
        if "VACUUM" in sql.upper():
            raise RuntimeError("database is locked")
        return await real_execute(sql, *a, **kw)

    await cache.set("k", {"a": 1}, ttl=timedelta(hours=1))
    conn = await cache._db.connect()
    monkeypatch.setattr(conn, "execute", explode_on_vacuum)

    assert await cache.clear() == 1
