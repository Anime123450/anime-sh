"""Library: metadata cache, favorites, history, and continue-watching joins —
against a real temp SQLite database."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from anime_sh.app.library import LibraryService
from anime_sh.domain.models import Anime, AnimeId, Format, Title, WatchProgress
from anime_sh.infra.db.database import Database
from anime_sh.infra.db.library import SqliteLibrary


@pytest.fixture
async def library(tmp_path: Path):
    db = Database(tmp_path / "anime.db", migrations_dir="migrations")
    await db.connect()
    yield SqliteLibrary(db)
    await db.close()


def _anime(anilist=154587, title="Frieren"):
    return Anime(
        id=AnimeId(anilist=anilist),
        title=Title(romaji=title, english=title),
        format=Format.TV,
        episode_count=28,
        genres=("Adventure", "Fantasy"),
    )


def _progress(anilist, episode, pos, completed=False):
    return WatchProgress(
        anime_id=AnimeId(anilist=anilist),
        episode=episode,
        position_s=pos,
        duration_s=1400,
        updated_at=datetime.now(timezone.utc),
        completed=completed,
    )


async def test_all_progress_lists_every_episode_for_one_show(library):
    await library.save_progress(_progress(1, 1.0, 1400, completed=True))
    await library.save_progress(_progress(1, 2.0, 700))
    await library.save_progress(_progress(2, 1.0, 100))  # different show
    rows = await library.all_progress(AnimeId(anilist=1))
    assert [(p.episode, p.completed) for p in rows] == [(1.0, True), (2.0, False)]
    assert await library.all_progress(AnimeId(anilist=99)) == []


async def test_mark_watched_catches_up_to_episode(library):
    svc = LibraryService(library)
    marked = await svc.mark_watched(_anime(1, "Frieren"), 3.0)
    assert marked == [1.0, 2.0, 3.0]
    rows = await library.all_progress(AnimeId(anilist=1))
    assert [(p.episode, p.completed) for p in rows] == [(1.0, True), (2.0, True), (3.0, True)]
    # Metadata cached so it renders offline.
    assert (await library.get_anime(AnimeId(anilist=1))) is not None
    # Completed episodes don't show up in continue-watching.
    assert await library.continue_watching() == []


async def test_stats_aggregates_history_and_progress(library):
    svc = LibraryService(library)
    a = _anime(1, "Frieren")  # genres: Adventure, Fantasy
    await library.save_anime(a)
    await library.save_progress(_progress(1, 1.0, 1400, completed=True))
    await library.save_progress(_progress(1, 2.0, 1400, completed=True))
    await library.save_progress(_progress(2, 1.0, 100))  # different show, unfinished
    await library.add_history(a.id, 1.0, provider="anikoto", seconds_watched=1400)
    await library.add_history(a.id, 2.0, provider="anikoto", seconds_watched=1300)

    s = await svc.stats()
    assert s.episodes_completed == 2
    assert s.shows == 2  # two distinct shows have progress
    assert s.sessions == 2
    assert s.total_seconds == 2700 and s.hours == round(2700 / 3600, 1)
    assert ("anikoto", 2) in s.top_providers
    assert ("Fantasy", 2) in s.top_genres  # weighted by the 2 history rows


async def test_mark_watched_single_episode(library):
    svc = LibraryService(library)
    marked = await svc.mark_watched(_anime(2, "X"), 5.0, single=True)
    assert marked == [5.0]
    rows = await library.all_progress(AnimeId(anilist=2))
    assert [p.episode for p in rows] == [5.0]


async def test_anime_cache_round_trip(library):
    await library.save_anime(_anime())
    got = await library.get_anime(AnimeId(anilist=154587))
    assert got is not None
    assert got.title.english == "Frieren"
    assert got.genres == ("Adventure", "Fantasy")


async def test_continue_watching_joins_cached_title(library):
    await library.save_anime(_anime())
    await library.save_progress(_progress(154587, 18.0, 640))
    items = await library.continue_watching()
    assert len(items) == 1
    assert items[0].anime.title.preferred == "Frieren"
    assert items[0].progress.episode == 18.0
    assert 0 < items[0].progress.fraction < 1


async def test_continue_watching_excludes_completed(library):
    await library.save_anime(_anime())
    await library.save_progress(_progress(154587, 1.0, 1390, completed=True))
    assert await library.continue_watching() == []


async def test_continue_watching_placeholder_when_uncached(library):
    # Progress exists but metadata was never cached -> placeholder, not a crash.
    await library.save_progress(_progress(999, 3.0, 100))
    items = await library.continue_watching()
    assert len(items) == 1
    assert items[0].anime.title.preferred == "anilist:999"


async def test_favorites_add_list_remove(library):
    svc = LibraryService(library)
    await svc.add_favorite(_anime())
    assert await library.is_favorite(AnimeId(anilist=154587)) is True
    favs = await svc.favorites()
    assert len(favs) == 1 and favs[0].anime.title.preferred == "Frieren"
    await svc.remove_favorite(AnimeId(anilist=154587))
    assert await svc.favorites() == []


async def test_history_records_and_lists(library):
    await library.save_anime(_anime())
    await library.add_history(AnimeId(anilist=154587), 5.0, provider="allanime", seconds_watched=800)
    await library.add_history(AnimeId(anilist=154587), 6.0, provider="allanime", seconds_watched=900)
    items = await library.list_history()
    assert len(items) == 2
    # Most recent first.
    assert items[0].episode == 6.0
    assert items[0].provider == "allanime"
    assert items[0].anime.title.preferred == "Frieren"
