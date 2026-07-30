"""Library: metadata cache, favorites, history, and continue-watching joins —
against a real temp SQLite database."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from anime_sh.app.library import LibraryService
from anime_sh.domain.models import Anime, AnimeId, Format, Status, Title, WatchProgress
from anime_sh.infra.db.database import Database
from anime_sh.infra.db.library import SqliteLibrary


@pytest.fixture
async def library(tmp_path: Path):
    db = Database(tmp_path / "anime.db", migrations_dir="migrations")
    await db.connect()
    yield SqliteLibrary(db)
    await db.close()


async def test_db_recovery_detects_and_rebuilds(tmp_path: Path):
    import sqlite3

    from anime_sh.infra.db.database import _is_healthy, _salvage_rebuild

    # A populated, healthy DB.
    db = Database(tmp_path / "anime.db", migrations_dir="migrations")
    await db.connect()
    lib = SqliteLibrary(db)
    await lib.save_progress(_progress(1, 3.0, 700, completed=True))
    await lib.save_progress(_progress(2, 1.0, 100))
    await db.close()
    path = tmp_path / "anime.db"
    assert _is_healthy(path)

    # Regression: reopening an existing WAL database must connect cleanly — the
    # integrity probe runs on the live connection, not a second one that would
    # race the WAL lock ("database is locked").
    db2 = Database(path, migrations_dir="migrations")
    conn = await db2.connect()
    cur = await conn.execute("SELECT COUNT(*) FROM progress")
    assert (await cur.fetchone())[0] == 2
    await db2.close()

    # A garbage file reads as corrupt.
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"SQLite format 3\x00" + b"\xde\xad\xbe\xef" * 500)
    assert not _is_healthy(junk)

    # Rebuild preserves every row and yields a healthy database.
    assert _salvage_rebuild(path) is True
    assert _is_healthy(path)
    conn2 = sqlite3.connect(str(path))
    assert conn2.execute("SELECT COUNT(*) FROM progress").fetchone()[0] == 2
    conn2.close()


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


async def test_save_progress_never_downgrades_recency(library):
    # A show watched here just now, then re-written by an AniList pull carrying
    # an older updatedAt: the newer completed flag applies, but the recency must
    # NOT drop back — otherwise the pull sinks a just-watched show down Continue
    # Watching.
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=3)
    aid = AnimeId(anilist=1)
    await library.save_progress(WatchProgress(
        anime_id=aid, episode=5.0, position_s=700, duration_s=1400,
        updated_at=now, completed=False))
    await library.save_progress(WatchProgress(
        anime_id=aid, episode=5.0, position_s=0, duration_s=0,
        updated_at=old, completed=True))
    row = next(p for p in await library.all_progress(aid) if p.episode == 5.0)
    assert row.updated_at == now      # recency kept, not downgraded to `old`
    assert row.completed is True      # but the newer completed flag applied


async def test_continue_watching_puts_locally_played_shows_first(library):
    # The reported bug: a background AniList sync reordered Continue Watching and
    # demoted the show you'd just watched here. Ordering is by local play history
    # (which the sync never touches), so a show played here outranks a synced-only
    # show even if the sync gave the latter a newer progress timestamp.
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=5)
    # Show 2: freshest progress timestamp, but never played here (synced only).
    await library.save_anime(_anime(2, "Synced Only"))
    await library.save_progress(WatchProgress(
        anime_id=AnimeId(anilist=2), episode=3.0, position_s=300, duration_s=1400,
        updated_at=now, completed=False))
    # Show 1: older progress, but actually played here → must rank first.
    await library.save_anime(_anime(1, "Watched Here"))
    await library.save_progress(WatchProgress(
        anime_id=AnimeId(anilist=1), episode=2.0, position_s=300, duration_s=1400,
        updated_at=old, completed=False))
    await library.add_history(AnimeId(anilist=1), 2.0, provider="anikoto",
                              seconds_watched=300)

    items = await library.continue_watching()
    assert [it.anime.id.anilist for it in items] == [1, 2]  # local play on top


async def test_mark_watched_catches_up_to_episode(library):
    svc = LibraryService(library)
    marked = await svc.mark_watched(_anime(1, "Frieren"), 3.0)
    assert marked == [1.0, 2.0, 3.0]
    rows = await library.all_progress(AnimeId(anilist=1))
    assert [(p.episode, p.completed) for p in rows] == [(1.0, True), (2.0, True), (3.0, True)]
    # Metadata cached so it renders offline.
    assert (await library.get_anime(AnimeId(anilist=1))) is not None
    # Catching up on an unfinished show keeps it in Continue Watching (you're
    # between episodes / waiting for the next), at the furthest episode marked.
    cont = await library.continue_watching()
    assert len(cont) == 1 and cont[0].progress.episode == 3.0


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


async def test_unmark_clears_only_that_show(library):
    svc = LibraryService(library)
    await svc.mark_watched(_anime(1, "A"), 3.0)
    await svc.mark_watched(_anime(2, "B"), 2.0)
    removed = await svc.unmark(AnimeId(anilist=1))
    assert removed == 3
    assert await library.all_progress(AnimeId(anilist=1)) == []
    assert len(await library.all_progress(AnimeId(anilist=2))) == 2  # untouched


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


async def test_continue_watching_keeps_completed_when_more_remain(library):
    # Finishing the latest episode of a show that isn't over must NOT drop it —
    # you're between episodes (caught up / next up), still "continuing" it.
    await library.save_anime(_anime())  # 28 eps, not marked finished
    await library.save_progress(_progress(154587, 1.0, 1390, completed=True))
    items = await library.continue_watching()
    assert len(items) == 1
    assert items[0].progress.episode == 1.0 and items[0].progress.completed is True


async def test_continue_watching_excludes_finished_and_fully_watched(library):
    # A series that has finished airing and you've watched to its last episode
    # is done — it drops off Continue Watching.
    done = Anime(id=AnimeId(anilist=7), title=Title(romaji="Done"),
                 format=Format.TV, status=Status.FINISHED, episode_count=3)
    await library.save_anime(done)
    await library.save_progress(_progress(7, 3.0, 1400, completed=True))
    assert await library.continue_watching() == []


async def test_continue_watching_keeps_partially_watched_finished_show(library):
    # Finished airing but you stopped at ep 5 of 12 — still continuable.
    half = Anime(id=AnimeId(anilist=8), title=Title(romaji="Half"),
                 format=Format.TV, status=Status.FINISHED, episode_count=12)
    await library.save_anime(half)
    await library.save_progress(_progress(8, 5.0, 1400, completed=True))
    items = await library.continue_watching()
    assert len(items) == 1 and items[0].progress.episode == 5.0


async def test_continue_watching_uses_furthest_episode(library):
    # Watched ep 1-2 fully then started ep 3: the card reflects the furthest
    # episode reached (3), not an earlier one.
    await library.save_anime(_anime())
    await library.save_progress(_progress(154587, 1.0, 1400, completed=True))
    await library.save_progress(_progress(154587, 2.0, 1400, completed=True))
    await library.save_progress(_progress(154587, 3.0, 300))
    items = await library.continue_watching()
    assert len(items) == 1 and items[0].progress.episode == 3.0


async def test_continue_watching_excludes_zero_position_imports(library):
    # An AniList-import row (progress but no local position) must not clutter
    # Continue Watching; only actually-started episodes show.
    await library.save_anime(_anime())
    await library.save_progress(_progress(154587, 3.0, 0))       # imported, pos=0
    await library.save_progress(_progress(154587, 4.0, 500))     # started locally
    items = await library.continue_watching()
    assert [it.progress.episode for it in items] == [4.0]


async def test_continue_watching_one_card_per_show(library):
    # Several in-progress episodes of the same show collapse to one card — the
    # most recently updated — instead of listing the show many times.
    await library.save_anime(_anime())
    import datetime as _dt
    for ep, pos, day in [(1.0, 300, 1), (2.0, 400, 2), (5.0, 200, 3)]:
        await library.save_progress(WatchProgress(
            anime_id=AnimeId(anilist=154587), episode=ep, position_s=pos,
            duration_s=1400, completed=False,
            updated_at=_dt.datetime(2026, 7, day, tzinfo=_dt.timezone.utc),
        ))
    items = await library.continue_watching()
    assert len(items) == 1 and items[0].progress.episode == 5.0  # latest


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
