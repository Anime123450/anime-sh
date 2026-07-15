"""Connection management + forward-only numbered migrations.

Migrations are plain ``.sql`` files applied in filename order. A
``schema_version`` table records which have run, so startup is idempotent.
Retrofitting migrations after you have users is miserable; this exists from
commit #1.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

_MIGRATIONS_ROOT = Path(__file__).parent


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
