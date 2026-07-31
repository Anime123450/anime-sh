"""Connection management + forward-only numbered migrations.

Migrations are plain ``.sql`` files applied in filename order. A
``schema_version`` table records which have run, so startup is idempotent.
Retrofitting migrations after you have users is miserable; this exists from
commit #1.
"""

from __future__ import annotations

import asyncio
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


def _scan_table(src: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    """Read every *readable* row of a table, skipping only the rows on a corrupt
    page rather than abandoning the rest of the table after the first bad one.

    This is the whole point of the salvage: corruption usually sits on one page,
    and the newest rows (highest rowid — a user's most recent watches) live at
    the *end* of the table. A plain ``SELECT *`` that raises mid-scan loses every
    row after the bad page, i.e. exactly the data you most want back. Instead we
    walk by ``rowid`` and, when a page faults, step past it and resume — so a bad
    page early in the file can't cost you the recent history that follows it.
    """
    try:
        cur = src.execute(f"SELECT * FROM {table} LIMIT 0")
        cols = [d[0] for d in cur.description]
    except Exception:
        return [], []
    rows: list[tuple] = []
    last = -1          # highest rowid successfully read so far
    misses = 0         # consecutive faults with no progress → widen the skip
    for _ in range(500_000):  # hard cap so a pathological file can't spin forever
        try:
            cur = src.execute(
                f"SELECT _rowid_ AS __rid, * FROM {table} "
                "WHERE _rowid_ > ? ORDER BY _rowid_",
                (last,),
            )
        except sqlite3.OperationalError:
            # WITHOUT ROWID table (no _rowid_) — one plain best-effort scan.
            try:
                cur = src.execute(f"SELECT * FROM {table}")
                return cols, [tuple(r[c] for c in cols) for r in cur]
            except Exception:
                return cols, rows
        progressed = False
        try:
            for r in cur:
                last = r["__rid"]
                rows.append(tuple(r[c] for c in cols))
                progressed = True
            return cols, rows  # reached the end with no fault
        except Exception:
            # Faulted on a page. Skip forward past the offending rowid and
            # resume; widen the jump geometrically if we keep landing on bad
            # rows so we escape a large damaged region instead of crawling.
            misses = 0 if progressed else misses + 1
            last += 1 << min(misses, 20)
    return cols, rows


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
        # Resilient per-table scan: a bad page costs only its own rows, never the
        # newer rows that follow it (see _scan_table).
        salvaged: dict[str, tuple[list[str], list[tuple]]] = {
            t: _scan_table(src, t) for t in tables
        }
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
    except sqlite3.DatabaseError as e:
        # Only genuine corruption should trigger the (destructive) salvage. A
        # transient lock/busy is *also* a DatabaseError subclass here, so key off
        # the message: "malformed"/"not a database"/"corrupt" mean rebuild;
        # anything else (locked, busy) is treated as healthy so we don't rebuild —
        # and risk losing rows — over a passing hiccup.
        msg = str(e).lower()
        if "malform" in msg or "not a database" in msg or "corrupt" in msg:
            return False
        return True
    except Exception:
        return True


def _set_aside_corrupt(path: Path) -> None:
    """Last resort when a rebuild isn't possible: move the corrupt file aside so
    the app starts fresh rather than crash-looping. The backup lets the data be
    recovered later."""
    try:
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        kept = path.with_suffix(f".db.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        path.rename(kept)
        # Name the backup: starting blank looks like "my whole library vanished",
        # so the user needs to know their data still exists and exactly where.
        log.warning(
            "database was damaged beyond repair; started fresh. Your previous "
            "library is kept at %s", kept
        )
    except Exception as e:  # pragma: no cover
        log.error("could not set aside corrupt database: %s", e)


class Database:
    """A single SQLite file with WAL enabled and migrations applied on open."""

    def __init__(self, path: Path, migrations_dir: str) -> None:
        self.path = path
        self._migrations_dir = _MIGRATIONS_ROOT / migrations_dir
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn
        # Serialise the first connect. Without this the check above is a
        # check-then-act race across an await: the home screen fans out ~20
        # metadata fetches at once, every one of them sees _conn as None, and each
        # opens its OWN connection. The extras then fight over the single writer
        # lock ("database is locked" on seasonal/trending), are never closed by
        # close(), and keep the file open while a recovery may be renaming it —
        # exactly the conditions that keep corrupting the database.
        async with self._lock:
            if self._conn is not None:
                return self._conn
            return await self._open()

    async def _open(self) -> aiosqlite.Connection:
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
        # Wait out a busy writer instead of failing the read. SQLite allows one
        # writer at a time, and a background AniList sync writing ~70 rows would
        # otherwise surface as "database is locked" on whatever the user was
        # loading at that moment.
        await conn.execute("PRAGMA busy_timeout=5000")
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
