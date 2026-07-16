"""SQLite-backed :class:`~anime_sh.domain.ports.DownloadStore`."""

from __future__ import annotations

from datetime import datetime, timezone

from ...domain.models import AnimeId, DownloadItem, DownloadStatus
from .database import Database
from .library import _ANIME_COLS, _anime_from_row, _placeholder


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteDownloadStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, anime_id: AnimeId, episode: float, path: str) -> int:
        conn = await self._db.connect()
        cur = await conn.execute(
            "INSERT INTO downloads (anilist_id, episode, path, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (anime_id.anilist, episode, path, DownloadStatus.QUEUED.value, _now()),
        )
        await conn.commit()
        return cur.lastrowid

    async def set_status(
        self, download_id: int, status: DownloadStatus, *, path: str | None = None
    ) -> None:
        conn = await self._db.connect()
        if path is not None:
            await conn.execute(
                "UPDATE downloads SET status=?, path=? WHERE id=?",
                (status.value, path, download_id),
            )
        else:
            await conn.execute(
                "UPDATE downloads SET status=? WHERE id=?", (status.value, download_id)
            )
        await conn.commit()

    async def list(self, *, limit: int = 50) -> list[DownloadItem]:
        conn = await self._db.connect()
        cur = await conn.execute(
            f"SELECT d.anilist_id, d.episode, d.path, d.status, d.created_at, "
            f"{_ANIME_COLS} FROM downloads d "
            "LEFT JOIN anime a ON a.anilist_id = d.anilist_id "
            "ORDER BY d.created_at DESC, d.id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [
            DownloadItem(
                anime=_anime_from_row(row) or _placeholder(row["anilist_id"]),
                episode=row["episode"],
                path=row["path"],
                status=DownloadStatus(row["status"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]
