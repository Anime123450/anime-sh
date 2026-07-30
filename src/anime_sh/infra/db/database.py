"""Connection management + forward-only numbered migrations.

Migrations are plain ``.sql`` files applied in filename order. A
``schema_version`` table records which have run, so startup is idempotent.
Retrofitting migrations after you have users is miserable; this exists from
commit #1.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

_MIGRATIONS_ROOT = Path(__file__).parent


def _is_healthy(path: Path) -> bool:
    """True if the SQLite file passes a quick integrity check."""
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False  # can't even read it → treat as corrupt


def _salvage_rebuild(path: Path) -> bool:
    """Rebuild a corrupt DB from what's still readable.

    Corruption is usually in the *indexes* while the table rows survive, so we
    table-scan each table (bypassing indexes), recreate the schema in a fresh
    file, re-insert the rows, and rebuild the indexes clean. On success the
    corrupt file is set aside with a ``.corrupt-<ts>`` suffix and the rebuilt
    file takes its place. Returns True if a healthy DB now sits at ``path``.
    """
    src = sqlite3.connect(str(path))
    src.row_factory = sqlite3.Row
    try:
        schema = [r[0] for r in src.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()]
        tables = [r[0] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()]
        salvaged: dict[str, tuple[list[str], list[tuple]]] = {}
        for t in tables:
            try:
                cur = src.execute(f"SELECT * FROM {t}")
                cols = [d[0] for d in cur.description]
                rows = []
                for r in cur:  # row-by-row: one bad page can't lose the table
                    try:
                        rows.append(tuple(r))
                    except Exception:
                        pass
                salvaged[t] = (cols, rows)
            except Exception as e:
                log.warning("db recovery: could not scan %s: %s", t, e)
    finally:
        src.close()

    rebuilt = path.with_suffix(".db.rebuilt")
    if rebuilt.exists():
        rebuilt.unlink()
    new = sqlite3.connect(str(rebuilt))
    try:
        def _skip(s: str) -> bool:
            return "sqlite_sequence" in s.lower()

        creates = [s for s in schema
                   if s.strip().upper().startswith("CREATE TABLE") and not _skip(s)]
        indexes = [s for s in schema
                   if s.strip().upper().startswith(("CREATE INDEX", "CREATE UNIQUE INDEX"))
                   and not _skip(s)]
        for s in creates:
            new.execute(s)
        for t, (cols, rows) in salvaged.items():
            if not cols or not rows:
                continue
            ph = ",".join("?" * len(cols))
            new.executemany(
                f"INSERT OR IGNORE INTO {t} ({','.join(cols)}) VALUES ({ph})", rows)
        new.commit()
        for s in indexes:
            try:
                new.execute(s)
            except Exception as e:
                log.warning("db recovery: index rebuild skipped: %s", e)
        new.commit()
        healthy = new.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        new.close()

    if not healthy:
        rebuilt.unlink(missing_ok=True)
        return False
    corrupt_keep = path.with_suffix(f".db.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    # Clear any stale WAL/journal sidecars so they can't reintroduce corruption.
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    path.rename(corrupt_keep)
    rebuilt.rename(path)
    log.warning("recovered corrupt database; original kept as %s", corrupt_keep.name)
    return True


async def _quick_check_ok(conn: aiosqlite.Connection) -> bool:
    """Integrity-probe the *already-open* connection. Doing this on the live
    connection (rather than opening a second one) is essential on Windows: a
    separate probe connection races the WAL lock and makes the real connect fail
    with 'database is locked'."""
    try:
        cur = await conn.execute("PRAGMA quick_check")
        row = await cur.fetchone()
        return bool(row) and row[0] == "ok"
    except Exception:
        return False


def _set_aside_corrupt(path: Path) -> None:
    """Last resort when a rebuild isn't possible: move the corrupt file aside so
    the app starts fresh rather than crash-looping. The backup lets the data be
    recovered later."""
    try:
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        path.rename(path.with_suffix(f".db.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"))
        log.warning("started with a fresh database; corrupt file kept as backup")
    except Exception as e:  # pragma: no cover
        log.error("could not set aside corrupt database: %s", e)


class Database:
    """A single SQLite file with WAL enabled and migrations applied on open."""

    def __init__(self, path: Path, migrations_dir: str) -> None:
        self.path = path
        self._migrations_dir = _MIGRATIONS_ROOT / migrations_dir
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path)
        # Self-heal a corrupt file: probe on THIS connection (a second probe
        # connection races the WAL lock on Windows → "database is locked"), and
        # if it's damaged, close, rebuild, and reopen the clean file. Otherwise a
        # silently-corrupt DB freezes the app — writes fail on the bad pages.
        if self.path.exists() and not await _quick_check_ok(conn):
            await conn.close()
            log.warning("database %s is corrupt; attempting recovery", self.path.name)
            try:
                if not _salvage_rebuild(self.path):
                    _set_aside_corrupt(self.path)
            except Exception as e:
                log.error("db recovery failed: %s", e)
                _set_aside_corrupt(self.path)
            conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate(conn)
        self._conn = conn
        return conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        cur = await conn.execute("SELECT version FROM schema_version")
        applied = {row[0] for row in await cur.fetchall()}

        for sql_file in sorted(self._migrations_dir.glob("*.sql")):
            version = int(sql_file.name.split("_", 1)[0])
            if version in applied:
                continue
            log.info("applying migration %s", sql_file.name)
            await conn.executescript(sql_file.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO schema_version (version, applied_at) "
                "VALUES (?, datetime('now'))",
                (version,),
            )
            await conn.commit()

    async def schema_version(self) -> int:
        conn = await self.connect()
        cur = await conn.execute("SELECT MAX(version) FROM schema_version")
        row = await cur.fetchone()
        return row[0] or 0
