"""SQLite-backed key/value cache with per-entry TTL.

Stores search results, trending/seasonal lists, and candidate lists. Resolved
`Stream` URLs are deliberately never cached here — they are IP- and time-bound.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db.database import Database


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KvCache:
    def __init__(self, db: Database) -> None:
        self._db = db

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

    async def purge_expired(self) -> int:
        conn = await self._db.connect()
        cur = await conn.execute(
            "DELETE FROM kv_cache WHERE expires_at < ?", (_now().isoformat(),)
        )
        await conn.commit()
        return cur.rowcount
