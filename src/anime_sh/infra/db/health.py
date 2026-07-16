"""SQLite-backed :class:`~anime_sh.domain.ports.HealthStore`.

Uses the ``provider_health`` table (created in migration 0001). Breaker state
lives here so a provider that tripped stays tripped across restarts.
"""

from __future__ import annotations

from datetime import datetime

from ...domain.models import BreakerState, ProviderHealth
from .database import Database


def _to_health(row) -> ProviderHealth:
    opened = row["opened_at"]
    return ProviderHealth(
        provider=row["provider"],
        state=BreakerState(row["state"]) if row["state"] else BreakerState.CLOSED,
        consecutive_failures=row["consecutive_failures"] or 0,
        opened_at=datetime.fromisoformat(opened) if opened else None,
    )


class SqliteHealthStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, provider: str) -> ProviderHealth | None:
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT provider, state, consecutive_failures, opened_at "
            "FROM provider_health WHERE provider=?",
            (provider,),
        )
        row = await cur.fetchone()
        return _to_health(row) if row else None

    async def save(self, health: ProviderHealth) -> None:
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO provider_health "
            "(provider, state, consecutive_failures, opened_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET "
            "state=excluded.state, "
            "consecutive_failures=excluded.consecutive_failures, "
            "opened_at=excluded.opened_at",
            (
                health.provider,
                health.state.value,
                health.consecutive_failures,
                health.opened_at.isoformat() if health.opened_at else None,
            ),
        )
        await conn.commit()

    async def all(self) -> list[ProviderHealth]:
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT provider, state, consecutive_failures, opened_at "
            "FROM provider_health ORDER BY provider"
        )
        return [_to_health(row) for row in await cur.fetchall()]
