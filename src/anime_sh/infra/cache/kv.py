"""SQLite-backed key/value cache with per-entry TTL.

Stores search results, trending/seasonal lists, and candidate lists. Resolved
`Stream` URLs are deliberately never cached here — they are IP- and time-bound.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db.database import Database


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Expired rows are otherwise only dropped when that exact key is read again, and
# a cache keyed by search query is mostly keys nobody types twice — a real
# install was found at 96% dead rows. Sweep them on a fixed cadence of writes so
# the file stays bounded without a maintenance command nobody knows to run.
_SWEEP_EVERY_WRITES = 50


class KvCache:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._writes_since_sweep = 0

    async def get(self, key: str) -> Any | None:
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT value_json, expires_at FROM kv_cache WHERE key=?", (key,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < _now():
            await conn.execute("DELETE FROM kv_cache WHERE key=?", (key,))
            await conn.commit()
            return None
        return json.loads(row["value_json"])

    async def set(self, key: str, value: Any, *, ttl: timedelta) -> None:
        conn = await self._db.connect()
        expires = (_now() + ttl).isoformat()
        await conn.execute(
            "INSERT INTO kv_cache (key, value_json, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value_json=excluded.value_json, expires_at=excluded.expires_at",
            (key, json.dumps(value), expires),
        )
        await conn.commit()
        self._writes_since_sweep += 1
        if self._writes_since_sweep >= _SWEEP_EVERY_WRITES:
            self._writes_since_sweep = 0
            # Best-effort housekeeping: never let tidying break a cache write,
            # which is itself only an optimisation.
            with contextlib.suppress(Exception):
                await self.purge_expired()

    async def purge_expired(self) -> int:
        conn = await self._db.connect()
        cur = await conn.execute(
            "DELETE FROM kv_cache WHERE expires_at < ?", (_now().isoformat(),)
        )
        await conn.commit()
        return cur.rowcount

    async def clear(self) -> int:
        """Drop every cached entry (fresh or stale). Returns rows removed."""
        conn = await self._db.connect()
        cur = await conn.execute("DELETE FROM kv_cache")
        await conn.commit()
        removed = cur.rowcount
        # DELETE leaves the freed pages inside the file, so a 1.5 MB cache stays
        # 1.5 MB after being emptied. Reclaiming disk is the usual reason for
        # running this, and a report of "cleared" next to an unchanged file size
        # reads like it did not work. Housekeeping only: a VACUUM that fails
        # (locked by another process, no room for the temp copy) must not turn a
        # successful clear into an error.
        with contextlib.suppress(Exception):
            await conn.execute("VACUUM")
            await conn.commit()
            # VACUUM rewrites the main file but leaves the write-ahead log where
            # it is, so in WAL mode the freed space simply moves to cache.db-wal
            # and the total on disk does not budge. Truncating the log is what
            # actually returns it.
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await conn.commit()
        return removed

    async def reclaimable_bytes(self) -> int:
        """Space the cache file is holding that nothing is using.

        Pruning frees pages inside the file without shrinking it — and it must
        stay that way, because pruning also runs as housekeeping after ordinary
        writes, where a VACUUM would be absurd. That leaves "3 entries, 1.5 MB"
        on screen, which looks broken until you can see that most of it is free
        space waiting for a `cache clear`.
        """
        conn = await self._db.connect()
        try:
            free = await (await conn.execute("PRAGMA freelist_count")).fetchone()
            size = await (await conn.execute("PRAGMA page_size")).fetchone()
        except Exception:
            return 0
        return int(free[0]) * int(size[0]) if free and size else 0

    async def stats(self) -> tuple[int, int]:
        """``(total_entries, expired_entries)``.

        Lets a caller say what clearing would actually cost before doing it —
        "1204 entries, 900 already stale" is something you can decide on, where a
        bare "are you sure?" is a guess.
        """
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(expires_at < ?), 0) FROM kv_cache",
            (_now().isoformat(),),
        )
        row = await cur.fetchone()
        return (row[0], row[1]) if row else (0, 0)
