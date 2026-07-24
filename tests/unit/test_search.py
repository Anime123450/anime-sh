"""SearchService — forgiving search: apostrophes, typos, and no regressions."""

from __future__ import annotations

import pytest

from anime_sh.app.search import SearchService
from anime_sh.domain.models import Anime, AnimeId, Title

from .fakes import make_anime


class FakeMeta:
    """Returns canned results per exact query string; records every call."""

    name = "fake"

    def __init__(self, table: dict[str, list[Anime]]) -> None:
        self._table = table
        self.calls: list[str] = []

    async def search(self, query: str, *, limit: int = 20) -> list[Anime]:
        self.calls.append(query)
        return list(self._table.get(query, []))

    async def get(self, id: AnimeId) -> Anime:
        return make_anime(id.anilist or 0)


async def test_working_query_is_untouched_and_makes_one_call():
    frieren = make_anime(154587, "Frieren: Beyond Journey's End")
    meta = FakeMeta({"frieren": [frieren]})
    svc = SearchService(meta)

    out = await svc.search("frieren")
    assert [r.anime.id.anilist for r in out] == [154587]
    assert meta.calls == ["frieren"]  # no escalation for a query that worked


async def test_missing_apostrophe_is_recovered():
    nagatoro = make_anime(101817, "DON'T TOY WITH ME, MISS NAGATORO")
    meta = FakeMeta({
        "dont toy with me": [],            # AniList's strict miss
        "don't toy with me": [nagatoro],   # apostrophe restored → hit
    })
    svc = SearchService(meta)

    out = await svc.search("dont toy with me")
    assert out and out[0].anime.id.anilist == 101817
    assert "don't toy with me" in meta.calls


async def test_best_match_recovers_possessive_via_distinctive_word():
    # "duke's" is a possessive, not a contraction, so apostrophe-restore can't
    # fix it. But the user spells "claims" correctly, so the distinctive-word
    # fallback finds the show by that and fuzzy-rank floats it to the top.
    duke = make_anime(208225, "The Duke's Son Claims He Won't Love Me")
    meta = FakeMeta({
        "dukes son claims he wont love me": [],   # AniList's strict miss
        "claims": [duke],                          # a correctly-spelled word hits
    })
    svc = SearchService(meta)

    hit = await svc.best_match("dukes son claims he wont love me")
    assert hit is not None and hit.id.anilist == 208225


async def test_typo_rescued_via_distinctive_word_and_fuzzy_rank():
    aot = make_anime(16498, "Attack on Titan")
    decoy = make_anime(999, "Titania")
    meta = FakeMeta({
        "atack on titan": [],          # primary miss (typo)
        "atack": [],                   # the typo'd word finds nothing
        "titan": [decoy, aot],         # distinctive word finds a pool
    })
    svc = SearchService(meta)

    out = await svc.search("atack on titan")
    # Fuzzy rank against what the user typed floats the real match to the top,
    # ahead of the incidental "Titania" the word search also returned.
    assert [r.anime.id.anilist for r in out][0] == 16498


async def test_nothing_anywhere_returns_empty():
    meta = FakeMeta({})  # every query misses
    svc = SearchService(meta)
    assert await svc.search("zzzznotathing") == []
    assert await svc.best_match("zzzznotathing") is None


async def test_single_word_query_does_not_word_fallback_on_itself():
    # A one-word query has nothing to fall back *through*, so we don't fire a
    # pointless self-search; only the raw (and any contraction) is tried.
    meta = FakeMeta({"friern": []})
    svc = SearchService(meta)
    out = await svc.search("friern")
    assert out == []
    assert meta.calls == ["friern"]  # no distinctive-word escalation
