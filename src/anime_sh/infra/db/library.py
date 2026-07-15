"""SQLite-backed :class:`~anime_sh.domain.ports.Library` — the sacred store."""

from __future__ import annotations

from datetime import datetime, timezone

from ...domain.models import AnimeId, WatchProgress
from .database import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteLibrary:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_progress(
        self, anime_id: AnimeId, episode: float
    ) -> WatchProgress | None:
        if anime_id.anilist is None:
            return None
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT position_s, duration_s, completed, updated_at "
            "FROM progress WHERE anilist_id=? AND episode=?",
            (anime_id.anilist, episode),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return WatchProgress(
            anime_id=anime_id,
            episode=episode,
            position_s=row["position_s"],
            duration_s=row["duration_s"],
            completed=bool(row["completed"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def save_progress(self, progress: WatchProgress) -> None:
        if progress.anime_id.anilist is None:
            return
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO progress "
            "(anilist_id, episode, position_s, duration_s, completed, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(anilist_id, episode) DO UPDATE SET "
            "position_s=excluded.position_s, duration_s=excluded.duration_s, "
            "completed=excluded.completed, updated_at=excluded.updated_at",
            (
                progress.anime_id.anilist,
                progress.episode,
                progress.position_s,
                progress.duration_s,
                int(progress.completed),
                progress.updated_at.isoformat(),
            ),
        )
        await conn.commit()

    async def continue_watching(
        self, *, limit: int = 20
    ) -> list[WatchProgress]:
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT anilist_id, episode, position_s, duration_s, completed, "
            "updated_at FROM progress WHERE completed=0 "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [
            WatchProgress(
                anime_id=AnimeId(anilist=row["anilist_id"]),
                episode=row["episode"],
                position_s=row["position_s"],
                duration_s=row["duration_s"],
                completed=bool(row["completed"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]
