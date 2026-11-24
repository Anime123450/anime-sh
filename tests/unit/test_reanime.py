"""Reanime provider: JSON parsing + the full search→episodes→candidates path
over a fake HTTP client (shapes captured from reanime.cz on 2026-11-24)."""

from __future__ import annotations

from anime_sh.domain.models import Anime, AnimeId, Audio, Title
from anime_sh.providers.reanime.provider import (
    ReanimeProvider,
    parse_episodes,
    parse_search,
    parse_servers,
)

SEARCH = {
    "results": [
        {"anime_id": "frieren-beyond-journey-s-end-yw2a3j", "anilist_id": 154587,
         "title": {"english": "Frieren: Beyond Journey’s End", "romaji": "Sousou no Frieren"},
         "episodes": 28, "subbed": 28, "dubbed": 28},
        {"anime_id": "frieren-beyond-journey-s-end-season-2-5pwd4d", "anilist_id": 182255,
         "title": {"english": "Frieren: Beyond Journey’s End Season 2",
                   "romaji": "Sousou no Frieren 2nd Season"},
         "episodes": 10, "subbed": 10, "dubbed": 0},
        {"anime_id": None, "anilist_id": 1, "title": {}},
    ]
}
EPISODES = {
    "data": [
        {"episode_number": 2, "subbed": True, "dubbed": False, "playable": True, "title": "B"},
        {"episode_number": 1, "subbed": True, "dubbed": True, "playable": True, "title": "A"},
        {"episode_number": 3, "subbed": True, "dubbed": True, "playable": False},
    ]
}
SERVERS = {
    "success": True,
    "servers": [
        {"serverName": "HD-1", "dataType": "sub", "dataLink": "https://flixcloud.cc/e/abc?v=1"},
        {"serverName": "HD-1", "dataType": "dub", "dataLink": "https://flixcloud.cc/e/abc?v=1"},
        {"serverName": "HD-2", "dataType": "sub", "dataLink": "https://flixcloud.cc/e/abc?v=2"},
        {"serverName": "HD-2", "dataType": "sub", "dataLink": "https://flixcloud.cc/e/abc?v=2"},
    ],
}

FRIEREN = Anime(
    id=AnimeId(anilist=154587, mal=52991),
    title=Title(romaji="Sousou no Frieren", english="Frieren: Beyond Journey's End"),
    episode_count=28,
)


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def get_json(self, url, *, params=None, headers=None):
        self.calls.append((url, params))
        if url.endswith("/api/v1/search"):
            return SEARCH
        if "/episodes" in url:
            return EPISODES
        if "/api/flix/" in url:
            return SERVERS
        raise AssertionError(url)


def test_parse_search_skips_rows_without_slug():
    items = parse_search(SEARCH)
    assert [i["id"] for i in items] == [
        "frieren-beyond-journey-s-end-yw2a3j|154587",
        "frieren-beyond-journey-s-end-season-2-5pwd4d|182255",
    ]
    assert items[0]["title"].startswith("Frieren") and items[1]["dub_eps"] == 0


def test_parse_episodes_drops_unplayable():
    assert [e["num"] for e in parse_episodes(EPISODES)] == [2.0, 1.0]


def test_parse_servers():
    assert [(s["type"], s["url"][-3:]) for s in parse_servers(SERVERS)] == [
        ("sub", "v=1"), ("dub", "v=1"), ("sub", "v=2"), ("sub", "v=2"),
    ]


async def test_exact_anilist_id_wins_over_fuzzy_titles():
    sources = await ReanimeProvider(http=FakeHttp()).find_sources(FRIEREN, Audio.SUB)
    assert [s.anime_key for s in sources] == ["frieren-beyond-journey-s-end-yw2a3j|154587"]
    assert sources[0].confidence == 1.0 and sources[0].provider == "reanime"


async def test_dub_search_hides_entries_with_no_dub():
    anime = Anime(id=AnimeId(anilist=182255), title=Title(romaji="Sousou no Frieren 2nd Season"))
    assert await ReanimeProvider(http=FakeHttp()).find_sources(anime, Audio.DUB) == []


async def test_episodes_and_candidates_end_to_end():
    http = FakeHttp()
    provider = ReanimeProvider(http=http)
    ref = await provider.match(FRIEREN, Audio.SUB)
    eps = await provider.episodes(ref, FRIEREN.id)
    assert [e.number for e in eps] == [1.0, 2.0]
    assert eps[0].episode_key == "154587|1|52991"

    cands = await provider.candidates(eps[0])
    # sub only, duplicate links collapsed
    assert [c.url for c in cands] == ["https://flixcloud.cc/e/abc?v=1", "https://flixcloud.cc/e/abc?v=2"]
    assert {c.host for c in cands} == {"flixcloud"}
    assert http.calls[-1] == ("https://reanime.cz/api/flix/154587/1", {"mal": "52991"})


async def test_dub_episodes_only_where_dubbed():
    provider = ReanimeProvider(http=FakeHttp())
    ref = await provider.match(FRIEREN, Audio.DUB)
    eps = await provider.episodes(ref, FRIEREN.id)
    assert [e.number for e in eps] == [1.0]
    cands = await provider.candidates(eps[0])
    assert [c.audio for c in cands] == [Audio.DUB]
