"""A migration that fails halfway must leave no trace.

`executescript` commits as it goes, so a script whose second statement failed
left the first one applied and the version row unwritten. The next startup
replayed the file, where `ALTER TABLE anime ADD COLUMN next_airing_episode`
raises "duplicate column name" — and `_migrate` runs inside `_open`, so that is
not a failed migration, it is an app that can never start again.

Verified against sqlite3 directly before fixing: a two-ALTER script whose second
statement fails leaves the first column added, and replaying it raises every
time.

The real trigger is not a typo — CI would catch that. It is a later statement
failing on one user's data (a UNIQUE index over rows that turn out to have
duplicates), or the process being killed between two auto-committed DDL
statements. 0002_next_airing.sql is exactly two ALTERs.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

import anime_sh.infra.db.database as mod
from anime_sh.infra.db.database import Database, _statements


@pytest.fixture
def migrations(tmp_path: Path, monkeypatch):
    """A migrations directory this test owns, rooted where Database looks."""
    monkeypatch.setattr(mod, "_MIGRATIONS_ROOT", tmp_path)
    d = tmp_path / "migs"
    d.mkdir()
    (d / "0001_initial.sql").write_text(
        "CREATE TABLE anime (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    return d


async def test_a_half_failing_migration_leaves_nothing_behind(migrations, tmp_path):
    """The bug, from the outside: the app stopped starting."""
    (migrations / "0002_two_alters.sql").write_text(
        "ALTER TABLE anime ADD COLUMN next_airing_episode INTEGER;\n"
        "ALTER TABLE anime ADD COLUMN next_airing_at TEXT, nonsense;\n",
        encoding="utf-8",
    )
    path = tmp_path / "anime.db"

    db = Database(path, migrations_dir="migs")
    with pytest.raises(sqlite3.OperationalError):
        await db.connect()
    await db.close()

    cols = _columns(path)
    assert "next_airing_episode" not in cols, (
        f"the first ALTER survived the failure ({cols}) — replaying this file "
        f"now raises 'duplicate column name' on every future startup"
    )


async def test_the_retry_after_a_fix_still_works(migrations, tmp_path):
    """What the bug made impossible: correcting the migration and moving on."""
    broken = migrations / "0002_two_alters.sql"
    broken.write_text(
        "ALTER TABLE anime ADD COLUMN next_airing_episode INTEGER;\n"
        "ALTER TABLE anime ADD COLUMN next_airing_at TEXT, nonsense;\n",
        encoding="utf-8",
    )
    path = tmp_path / "anime.db"
    db = Database(path, migrations_dir="migs")
    with pytest.raises(sqlite3.OperationalError):
        await db.connect()
    await db.close()

    broken.write_text(
        "ALTER TABLE anime ADD COLUMN next_airing_episode INTEGER;\n"
        "ALTER TABLE anime ADD COLUMN next_airing_at TEXT;\n",
        encoding="utf-8",
    )
    db = Database(path, migrations_dir="migs")
    await db.connect()
    try:
        assert await db.schema_version() == 2
    finally:
        await db.close()
    assert {"next_airing_episode", "next_airing_at"} <= set(_columns(path))


async def test_a_good_migration_is_still_applied_and_recorded(migrations, tmp_path):
    (migrations / "0002_next_airing.sql").write_text(
        "-- a comment with a ; semicolon in it\n"
        "ALTER TABLE anime ADD COLUMN next_airing_episode INTEGER;\n"
        "ALTER TABLE anime ADD COLUMN next_airing_at TEXT;\n",
        encoding="utf-8",
    )
    db = Database(tmp_path / "anime.db", migrations_dir="migs")
    await db.connect()
    try:
        assert await db.schema_version() == 2
    finally:
        await db.close()
    assert {"next_airing_episode", "next_airing_at"} <= set(
        _columns(tmp_path / "anime.db")
    )


async def test_a_failed_migration_does_not_leak_its_connection(migrations, tmp_path):
    """Found by the revert check hanging.

    `_open` assigns `self._conn` only after migrating, so a migration that raised
    left an open connection nothing could close — and aiosqlite runs each one on
    its own non-daemon thread, so the process would not exit. On Windows it also
    holds the file open while a later recovery may be renaming it.
    """
    (migrations / "0002_broken.sql").write_text(
        "ALTER TABLE anime ADD COLUMN a INTEGER, nonsense;\n", encoding="utf-8"
    )
    # The giveaway is aiosqlite's worker thread still running the connection,
    # named after its own target function — the only handle on a connection
    # nothing holds a reference to any more. Counted as a *difference* so an
    # unrelated test's open database cannot fail this one.
    import threading

    def workers() -> set[int]:
        return {
            t.ident for t in threading.enumerate()
            if t.is_alive() and "_connection_worker_thread" in t.name
        }

    before = workers()
    db = Database(tmp_path / "anime.db", migrations_dir="migs")
    with pytest.raises(sqlite3.OperationalError):
        await db.connect()

    # aiosqlite's close() does not join its thread, so give it a moment to wind
    # down. Two seconds is generous for a thread that is already told to stop and
    # is not long enough to hide a connection that was never closed at all.
    for _ in range(100):
        if not workers() - before:
            break
        await asyncio.sleep(0.02)
    assert not workers() - before, "the failed migration left its connection open"


def test_statements_are_split_the_way_sqlite_reads_them():
    script = (
        "-- leading comment; with a semicolon\n"
        "CREATE TABLE t (a TEXT DEFAULT 'x;y');\n"
        "INSERT INTO t (a) VALUES ('p;q');\n"
    )
    out = [s.strip() for s in _statements(script)]
    assert len(out) == 2, out
    assert out[0].endswith("'x;y');") and out[1].startswith("INSERT")


def test_a_trailing_statement_without_a_semicolon_is_not_dropped():
    out = _statements("CREATE TABLE t (a TEXT)\n")
    assert len(out) == 1 and "CREATE TABLE" in out[0]


def _columns(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(anime)")]
    finally:
        conn.close()
