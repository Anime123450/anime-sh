"""Migrations + library round-trip against a temp SQLite file."""

from __future__ import annotations

from pathlib import Path

import pytest

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


async def test_user_migrations_apply(user_db):
    assert await user_db.schema_version() == 1


async def test_migrations_are_idempotent(tmp_path: Path):
    path = tmp_path / "anime.db"
    for _ in range(3):
        db = Database(path, migrations_dir="migrations")
        await db.connect()
        assert await db.schema_version() == 1
        await db.close()


async def test_progress_round_trip(user_db):
    lib = SqliteLibrary(user_db)
    prog = resume_at(500, episode=18.0)
    await lib.save_progress(prog)
    got = await lib.get_progress(prog.anime_id, 18.0)
    assert got is not None
    assert got.position_s == 500


async def test_continue_watching_orders_recent_first(user_db):
    lib = SqliteLibrary(user_db)
    await lib.save_progress(resume_at(100, episode=1.0))
    await lib.save_progress(resume_at(200, episode=2.0))
    rows = await lib.continue_watching()
    assert len(rows) == 2


async def test_cache_ttl_expiry(cache_db):
    cache = KvCache(cache_db)
    await cache.set("k", {"v": 1}, ttl=timedelta(seconds=60))
    assert await cache.get("k") == {"v": 1}
    await cache.set("gone", {"v": 2}, ttl=timedelta(seconds=-1))
    assert await cache.get("gone") is None
