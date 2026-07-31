"""Migrations + library round-trip against a temp SQLite file."""

from __future__ import annotations

from pathlib import Path

import pytest

import anime_sh.infra.db
from anime_sh.infra.db.database import Database
from anime_sh.infra.db.library import SqliteLibrary
from anime_sh.infra.cache.kv import KvCache

from .fakes import resume_at
from datetime import timedelta


@pytest.fixture
async def user_db(tmp_path: Path):
    db = Database(tmp_path / "anime.db", migrations_dir="migrations")
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
async def cache_db(tmp_path: Path):
    db = Database(tmp_path / "cache.db", migrations_dir="migrations_cache")
    await db.connect()
    yield db
    await db.close()


def _latest_migration(name: str) -> int:
    """Highest numbered migration on disk — so adding one doesn't break these."""
    root = Path(anime_sh.infra.db.__file__).parent / name
    return max(int(p.name.split("_", 1)[0]) for p in root.glob("*.sql"))


async def test_concurrent_connect_opens_exactly_one_connection(tmp_path: Path):
    """Twenty callers racing to first-connect must share ONE connection.

    ``connect`` checked ``self._conn is None`` and then awaited, so concurrent
    callers (the home screen fans out ~20 metadata fetches at once) each opened
    their own connection. The extras fought over SQLite's single writer lock —
    surfacing as "database is locked" — were never closed, and held the file open
    while a recovery might be renaming it.
    """
    import asyncio

    db = Database(tmp_path / "anime.db", migrations_dir="migrations")
    conns = await asyncio.gather(*(db.connect() for _ in range(20)))
    assert len({id(c) for c in conns}) == 1
    # And closing really releases it — no leaked connection left behind.
    await db.close()
    assert db._conn is None


async def test_user_migrations_apply(user_db):
    assert await user_db.schema_version() == _latest_migration("migrations")


async def test_migrations_are_idempotent(tmp_path: Path):
    path = tmp_path / "anime.db"
    for _ in range(3):
        db = Database(path, migrations_dir="migrations")
        await db.connect()
        # Re-opening must not re-apply or skip anything.
        assert await db.schema_version() == _latest_migration("migrations")
        await db.close()


async def test_progress_round_trip(user_db):
    lib = SqliteLibrary(user_db)
    prog = resume_at(500, episode=18.0)
    await lib.save_progress(prog)
    got = await lib.get_progress(prog.anime_id, 18.0)
    assert got is not None
    assert got.position_s == 500


async def test_continue_watching_orders_recent_first(user_db):
    from datetime import datetime, timezone

    from anime_sh.domain.models import AnimeId, WatchProgress

    lib = SqliteLibrary(user_db)

    def prog(anilist, day):
        return WatchProgress(
            anime_id=AnimeId(anilist=anilist), episode=1.0, position_s=100,
            duration_s=1400, completed=False,
            updated_at=datetime(2026, 7, day, tzinfo=timezone.utc),
        )

    # Two different shows — one card each, newest first.
    await lib.save_progress(prog(111, 1))
    await lib.save_progress(prog(222, 2))
    rows = await lib.continue_watching()
    assert [r.anime.id.anilist for r in rows] == [222, 111]


async def test_cache_ttl_expiry(cache_db):
    cache = KvCache(cache_db)
    await cache.set("k", {"v": 1}, ttl=timedelta(seconds=60))
    assert await cache.get("k") == {"v": 1}
    await cache.set("gone", {"v": 2}, ttl=timedelta(seconds=-1))
    assert await cache.get("gone") is None
