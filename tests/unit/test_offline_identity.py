"""Commands that name a show keep working when AniList does not.

On 07/09/2026 AniList disabled its public API for days. `play` already fell
back to the local library to identify a show, but `sources`, `download`,
`favorite add/rm` and `search` called AniList directly and died on a raw
``POST https://graphql.anilist.co -> 403`` — for shows sitting in the user's own
library, one command over from the one that worked.

These drive the real command functions with AniList dead and a library that
knows the show. Every write lands on a fake, never on a real database.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import typer

from anime_sh.cli import main as cli_main
from anime_sh.domain.errors import MetadataError
from anime_sh.domain.models import Anime, AnimeId, Status, Title

FRIEREN = Anime(
    id=AnimeId(anilist=154587),
    title=Title(romaji="Sousou no Frieren", english="Frieren: Beyond Journey's End"),
    episode_count=28,
    status=Status.FINISHED,
)
DOWN = "AniList: The AniList API has been temporarily disabled."


class _DeadSearch:
    async def best_match(self, query):
        raise MetadataError(DOWN)

    async def search(self, query, limit=20):
        raise MetadataError(DOWN)


class _DeadMetadata:
    async def search_filtered(self, *a, **k):
        raise MetadataError(DOWN)


class _Library:
    def __init__(self, known):
        self.known = known

    async def find_anime_by_title(self, query):
        return [a for a in self.known if query.lower() in a.title.preferred.lower()]


class _LibraryService:
    def __init__(self):
        self.favorited, self.unfavorited = [], []

    async def add_favorite(self, anime):
        self.favorited.append(anime.id)

    async def remove_favorite(self, anime_id):
        self.unfavorited.append(anime_id)


class _Playback:
    def __init__(self):
        self.asked_for = []

    async def list_sources(self, anime, *, audio):
        self.asked_for.append(anime.id)
        return []


class _Download:
    """ffmpeg present, every episode already on disk — so nothing is fetched."""

    def __init__(self):
        self.asked_for = []

    def available(self):
        return True

    def local_path(self, anime, n):
        self.asked_for.append((anime.id, n))
        return "/already/here.mp4"


class _Container:
    def __init__(self, known=(FRIEREN,)):
        self.search = _DeadSearch()
        self.metadata = _DeadMetadata()
        self.library = _Library(list(known))
        self.library_service = _LibraryService()
        self.playback = _Playback()
        self.download = _Download()
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture
def offline(monkeypatch):
    fake = _Container()
    monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: fake)
    return fake


@pytest.fixture
def offline_empty(monkeypatch):
    fake = _Container(known=())
    monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: fake)
    return fake


def test_sources_identifies_the_show_from_the_library(offline):
    asyncio.run(cli_main._sources("Frieren", False, False))
    assert offline.playback.asked_for == [FRIEREN.id]


def test_favorite_add_and_rm_act_on_the_library_show(offline):
    asyncio.run(cli_main._favorite_add("Frieren"))
    asyncio.run(cli_main._favorite_rm("Frieren"))
    assert offline.library_service.favorited == [FRIEREN.id]
    assert offline.library_service.unfavorited == [FRIEREN.id]


def test_download_identifies_the_show_from_the_library(offline):
    asyncio.run(cli_main._download("Frieren", "1-2", False, None))
    assert offline.download.asked_for == [(FRIEREN.id, 1.0), (FRIEREN.id, 2.0)]


def test_a_show_not_in_the_library_still_reports_the_outage(offline_empty):
    """The fallback must not turn "AniList is down" into "no such show"."""
    with pytest.raises(MetadataError, match="temporarily disabled"):
        asyncio.run(cli_main._sources("Frieren", False, False))


def test_search_lists_library_matches(offline, capsys):
    asyncio.run(
        cli_main._search("frieren", None, None, None, None, None, 20, True)
    )
    out, errout = capsys.readouterr()
    # --json output stays machine-readable: the notice goes to stderr only.
    assert [a["anilist_id"] for a in json.loads(out)] == [154587]
    assert "local library" in errout


def test_filtered_search_has_nothing_local_to_fall_back_to(offline):
    """Browsing by genre is a query over AniList's whole catalogue. Answering it
    with "the shows you happen to have watched" would be a wrong answer, not a
    degraded one."""
    with pytest.raises(typer.Exit) as ei:
        asyncio.run(
            cli_main._search(None, ["action"], None, None, None, None, 20, False)
        )
    assert ei.value.exit_code == 2


def test_search_with_no_library_match_still_exits_with_the_outage(offline_empty):
    with pytest.raises(typer.Exit) as ei:
        asyncio.run(
            cli_main._search("frieren", None, None, None, None, None, 20, False)
        )
    assert ei.value.exit_code == 2
