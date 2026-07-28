"""SearchService — forgiving search: apostrophes, typos, and no regressions."""

from __future__ import annotations

import pytest

from anime_sh.app.search import SearchService
from anime_sh.domain.models import Anime, AnimeId, Title

from .fakes import make_anime


class FakeMeta:
    """Returns canned results per exact query string; records every call.

    ``catalog`` seeds the local popularity index exposed via ``popular`` (pass
    the string ``"boom"`` to make the index build raise, for the degrade test).
    """

    name = "fake"

    def __init__(
        self,
        table: dict[str, list[Anime]],
        *,
        catalog: list[Anime] | str | None = None,
    ) -> None:
        self._table = table
        self._catalog = catalog
        self.calls: list[str] = []
        self.popular_calls = 0

    async def search(self, query: str, *, limit: int = 20) -> list[Anime]:
        self.calls.append(query)
        return list(self._table.get(query, []))

    async def popular(self, *, limit: int = 500) -> list[Anime]:
        self.popular_calls += 1
        if self._catalog == "boom":
            raise RuntimeError("index build failed")
        return list(self._catalog or [])

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


# -- local popularity index (rescues what AniList's strict search drops) ----- #

def _catalog() -> list[Anime]:
    return [
        make_anime(1, "The Eminence in Shadow"),
        make_anime(21, "One Piece"),
        make_anime(154587, "Sousou no Frieren"),
        make_anime(20, "Naruto"),
    ]


async def test_stopword_query_rescued_by_index():
    # AniList drops "the" as a full-text stopword → empty. The index still finds
    # the popular shows whose title contains it.
    meta = FakeMeta({}, catalog=_catalog())
    svc = SearchService(meta)
    out = await svc.search("the")
    ids = [r.anime.id.anilist for r in out]
    assert 1 in ids  # "The Eminence in Shadow"
    assert 20 not in ids  # "Naruto" has nothing to do with "the"


async def test_word_fragment_rescued_by_index():
    # "fri" is a mid-title fragment AniList won't match; the index prefixes it.
    meta = FakeMeta({}, catalog=_catalog())
    svc = SearchService(meta)
    out = await svc.search("fri")
    assert [r.anime.id.anilist for r in out] == [154587]  # Sousou no Frieren


async def test_despaced_query_rescued_by_index():
    # "onepiece" is one dead token to AniList; squashed matching finds One Piece.
    meta = FakeMeta({}, catalog=_catalog())
    svc = SearchService(meta)
    out = await svc.search("onepiece")
    assert [r.anime.id.anilist for r in out] == [21]


async def test_working_query_never_builds_the_index():
    # The fast path must stay untouched: a hit means no index build, ever.
    frieren = make_anime(154587, "Frieren")
    meta = FakeMeta({"frieren": [frieren]}, catalog=_catalog())
    svc = SearchService(meta)
    await svc.search("frieren")
    assert meta.popular_calls == 0


async def test_index_is_built_at_most_once():
    meta = FakeMeta({}, catalog=_catalog())
    svc = SearchService(meta)
    await svc.search("the")
    await svc.search("fri")
    assert meta.popular_calls == 1  # memoised for the service's lifetime


async def test_index_build_failure_degrades_cleanly():
    # If the catalog can't be fetched, search falls back to empty, never crashes.
    meta = FakeMeta({}, catalog="boom")
    svc = SearchService(meta)
    assert await svc.search("the") == []


async def test_despaced_variant_also_hits_anilist():
    # De-glued camelCase is retried against AniList too (helps long-tail shows
    # that aren't in the popularity index).
    rezero = make_anime(21355, "Re:Zero kara Hajimeru Isekai Seikatsu")
    meta = FakeMeta({"ReZero": [], "Re Zero": [rezero]})
    svc = SearchService(meta)
    out = await svc.search("ReZero")
    assert out and out[0].anime.id.anilist == 21355
    assert "Re Zero" in meta.calls
