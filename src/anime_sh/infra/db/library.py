"""SQLite-backed :class:`~anime_sh.domain.ports.Library` — the sacred store.

Also caches AniList metadata (the ``anime`` table) for anything the user
touches, so continue-watching / history / favorites render titles and cover art
even when the network and every provider are down.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ...domain.models import (
    Anime,
    AnimeId,
    FavoriteItem,
    Format,
    HistoryItem,
    ResumeItem,
    Season,
    Status,
    Title,
    WatchProgress,
)
from .database import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enum(cls, value, default):
    if value is None:
        return default
    try:
        return cls(value)
    except ValueError:
        return default


def _anime_from_row(row) -> Anime | None:
    """Build an Anime from a joined ``anime`` row, or None if not cached.

    A LEFT JOIN miss yields a row whose aliased ``a_anilist_id`` is NULL.
    """
    if row is None or row["a_anilist_id"] is None:
        return None
    return Anime(
        id=AnimeId(anilist=row["a_anilist_id"], mal=row["mal_id"]),
        title=Title(
            romaji=row["title_romaji"],
            english=row["title_english"],
            native=row["title_native"],
        ),
        format=_enum(Format, row["format"], Format.UNKNOWN),
        status=_enum(Status, row["status"], Status.UNKNOWN),
        episode_count=row["episodes"],
        season=_enum(Season, row["season"], None) if row["season"] else None,
        year=row["year"],
        genres=tuple(json.loads(row["genres_json"]) if row["genres_json"] else ()),
        cover_url=row["cover_url"],
        synopsis=row["synopsis"],
    )


def _placeholder(anilist_id: int) -> Anime:
    return Anime(id=AnimeId(anilist=anilist_id), title=Title(romaji=f"anilist:{anilist_id}"))


# Columns aliased so joins never collide with the driving table's anilist_id.
_ANIME_COLS = (
    "a.anilist_id AS a_anilist_id, a.mal_id, a.title_romaji, a.title_english, "
    "a.title_native, a.format, a.status, a.episodes, a.season, a.year, "
    "a.cover_url, a.synopsis, a.genres_json"
)


class SqliteLibrary:
    def __init__(self, db: Database) -> None:
        self._db = db

    # -- progress ----------------------------------------------------------- #
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

    async def delete_progress(self, anime_id: AnimeId) -> int:
        if anime_id.anilist is None:
            return 0
        conn = await self._db.connect()
        cur = await conn.execute(
            "DELETE FROM progress WHERE anilist_id=?", (anime_id.anilist,)
        )
        await conn.commit()
        return cur.rowcount

    async def all_progress(self, anime_id: AnimeId) -> list[WatchProgress]:
        if anime_id.anilist is None:
            return []
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT episode, position_s, duration_s, completed, updated_at "
            "FROM progress WHERE anilist_id=? ORDER BY episode",
            (anime_id.anilist,),
        )
        rows = await cur.fetchall()
        return [
            WatchProgress(
                anime_id=anime_id,
                episode=row["episode"],
                position_s=row["position_s"],
                duration_s=row["duration_s"],
                completed=bool(row["completed"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    async def all_progress_rows(self) -> list[WatchProgress]:
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT anilist_id, episode, position_s, duration_s, completed, "
            "updated_at FROM progress ORDER BY anilist_id, episode"
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

    async def continue_watching(self, *, limit: int = 20) -> list[ResumeItem]:
        conn = await self._db.connect()
        cur = await conn.execute(
            f"SELECT p.anilist_id, p.episode, p.position_s, p.duration_s, "
            f"p.completed, p.updated_at, {_ANIME_COLS} "
            "FROM progress p "
            # One card per show: the most recently-updated in-progress episode.
            "JOIN (SELECT anilist_id, MAX(updated_at) AS mu FROM progress "
            "      WHERE completed=0 AND position_s > 0 GROUP BY anilist_id) g "
            "  ON g.anilist_id = p.anilist_id AND g.mu = p.updated_at "
            "LEFT JOIN anime a ON a.anilist_id = p.anilist_id "
            # position_s>0 keeps this to episodes actually started here — an
            # AniList import (progress but no local position) belongs on the My
            # List screen, not cluttering Continue Watching at 0%.
            "WHERE p.completed=0 AND p.position_s > 0 "
            "GROUP BY p.anilist_id ORDER BY p.updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        items: list[ResumeItem] = []
        for row in rows:
            anime = _anime_from_row(row) or _placeholder(row["anilist_id"])
            items.append(
                ResumeItem(
                    anime=anime,
                    progress=WatchProgress(
                        anime_id=anime.id,
                        episode=row["episode"],
                        position_s=row["position_s"],
                        duration_s=row["duration_s"],
                        completed=bool(row["completed"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]),
                    ),
                )
            )
        return items

    # -- metadata cache ----------------------------------------------------- #
    async def save_anime(self, anime: Anime) -> None:
        if anime.id.anilist is None:
            return
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO anime (anilist_id, mal_id, title_romaji, title_english, "
            "title_native, format, status, episodes, season, year, cover_url, "
            "synopsis, genres_json, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(anilist_id) DO UPDATE SET "
            "mal_id=excluded.mal_id, title_romaji=excluded.title_romaji, "
            "title_english=excluded.title_english, title_native=excluded.title_native, "
            "format=excluded.format, status=excluded.status, episodes=excluded.episodes, "
            "season=excluded.season, year=excluded.year, cover_url=excluded.cover_url, "
            "synopsis=excluded.synopsis, genres_json=excluded.genres_json, "
            "fetched_at=excluded.fetched_at",
            (
                anime.id.anilist, anime.id.mal,
                anime.title.romaji, anime.title.english, anime.title.native,
                anime.format.value, anime.status.value, anime.episode_count,
                anime.season.value if anime.season else None, anime.year,
                anime.cover_url, anime.synopsis,
                json.dumps(list(anime.genres)), _now(),
            ),
        )
        await conn.commit()

    async def get_anime(self, anime_id: AnimeId) -> Anime | None:
        if anime_id.anilist is None:
            return None
        conn = await self._db.connect()
        cur = await conn.execute(
            f"SELECT {_ANIME_COLS} FROM anime a WHERE a.anilist_id=?",
            (anime_id.anilist,),
        )
        return _anime_from_row(await cur.fetchone())

    # -- history ------------------------------------------------------------ #
    async def add_history(
        self, anime_id: AnimeId, episode: float, *, provider: str | None,
        seconds_watched: int,
    ) -> None:
        if anime_id.anilist is None:
            return
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO history (anilist_id, episode, watched_at, provider, "
            "seconds_watched) VALUES (?, ?, ?, ?, ?)",
            (anime_id.anilist, episode, _now(), provider, seconds_watched),
        )
        await conn.commit()

    async def list_history(self, *, limit: int = 50) -> list[HistoryItem]:
        conn = await self._db.connect()
        cur = await conn.execute(
            f"SELECT h.anilist_id, h.episode, h.watched_at, h.provider, "
            f"h.seconds_watched, {_ANIME_COLS} "
            "FROM history h LEFT JOIN anime a ON a.anilist_id = h.anilist_id "
            # Tiebreak on id: Windows' clock granularity can stamp rapid inserts
            # with identical timestamps, so watched_at alone isn't deterministic.
            "ORDER BY h.watched_at DESC, h.id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [
            HistoryItem(
                anime=_anime_from_row(row) or _placeholder(row["anilist_id"]),
                episode=row["episode"],
                watched_at=datetime.fromisoformat(row["watched_at"]),
                provider=row["provider"],
                seconds_watched=row["seconds_watched"],
            )
            for row in rows
        ]

    # -- favorites ---------------------------------------------------------- #
    async def add_favorite(self, anime_id: AnimeId, *, note: str | None = None) -> None:
        if anime_id.anilist is None:
            return
        conn = await self._db.connect()
        await conn.execute(
            "INSERT INTO favorites (anilist_id, added_at, note) VALUES (?, ?, ?) "
            "ON CONFLICT(anilist_id) DO UPDATE SET note=excluded.note",
            (anime_id.anilist, _now(), note),
        )
        await conn.commit()

    async def remove_favorite(self, anime_id: AnimeId) -> None:
        if anime_id.anilist is None:
            return
        conn = await self._db.connect()
        await conn.execute(
            "DELETE FROM favorites WHERE anilist_id=?", (anime_id.anilist,)
        )
        await conn.commit()

    async def is_favorite(self, anime_id: AnimeId) -> bool:
        if anime_id.anilist is None:
            return False
        conn = await self._db.connect()
        cur = await conn.execute(
            "SELECT 1 FROM favorites WHERE anilist_id=?", (anime_id.anilist,)
        )
        return await cur.fetchone() is not None

    async def list_favorites(self) -> list[FavoriteItem]:
        conn = await self._db.connect()
        cur = await conn.execute(
            f"SELECT f.anilist_id, f.added_at, f.note, {_ANIME_COLS} "
            "FROM favorites f LEFT JOIN anime a ON a.anilist_id = f.anilist_id "
            "ORDER BY f.added_at DESC"
        )
        rows = await cur.fetchall()
        return [
            FavoriteItem(
                anime=_anime_from_row(row) or _placeholder(row["anilist_id"]),
                added_at=datetime.fromisoformat(row["added_at"]),
                note=row["note"],
            )
            for row in rows
        ]
