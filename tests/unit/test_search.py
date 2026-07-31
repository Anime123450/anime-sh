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


def _titled(anilist, english=None, romaji=None, synonyms=(), popularity=0):
    from anime_sh.domain.models import Anime, AnimeId, Title

    return Anime(
        id=AnimeId(anilist=anilist),
        title=Title(romaji=romaji or english, english=english, synonyms=tuple(synonyms)),
        popularity=popularity,
    )


def test_exact_title_beats_a_more_popular_near_match():
    """Validated against 500 real titles: every show must rank first for its own
    title. These two only differ by punctuation, which folding erases, so the
    more popular season used to win no matter which one you typed."""
    from anime_sh.app.search import _ranked

    s1 = _titled(1, "Nisekoi", popularity=500_000)
    s2 = _titled(2, "Nisekoi:", popularity=200_000)
    pool = [s1, s2]
    assert _ranked(pool, "Nisekoi:", 1)[0].id.anilist == 2
    assert _ranked(pool, "Nisekoi", 1)[0].id.anilist == 1

    k1 = _titled(3, "Kaguya-sama: Love is War", popularity=600_000)
    k2 = _titled(4, "Kaguya-sama: Love is War?", popularity=400_000)
    assert _ranked([k1, k2], "Kaguya-sama: Love is War?", 1)[0].id.anilist == 4


def test_fullwidth_and_unicode_queries_fold_to_ascii():
    """`_norm` stripped every non-ASCII character, so a fullwidth query folded
    to nothing and matched nothing."""
    from anime_sh.app.search import _norm, _ranked, _squash

    assert _norm("ＮＡＲＵＴＯ") == "naruto"
    assert _squash("ＮＡＲＵＴＯ") == "naruto"
    pool = [_titled(1, "Naruto", popularity=900_000), _titled(2, "Bleach")]
    assert _ranked(pool, "ＮＡＲＵＴＯ", 1)[0].id.anilist == 1


def test_a_hugely_more_popular_prefix_match_beats_an_obscure_exact_title():
    """The case that forced ranking to be a blend rather than strict tiers.

    "Hello Again, JoJo" (popularity 89) has the romaji title "JoJo", so it is a
    genuine exact match and buried JoJo's Bizarre Adventure (popularity 470k),
    whose title merely starts with the query. Exactness still has to win for
    "Nisekoi:" vs "Nisekoi", so neither can dominate unconditionally.
    """
    from anime_sh.app.search import _ranked

    obscure_exact = _titled(1, romaji="JoJo", popularity=89)
    famous_prefix = _titled(2, "JoJo's Bizarre Adventure (TV)", popularity=470_694)
    assert _ranked([obscure_exact, famous_prefix], "JoJo", 1)[0].id.anilist == 2

    # …but a modest popularity gap must not override an exact title.
    s1 = _titled(3, "Nisekoi", popularity=500_000)
    s2 = _titled(4, "Nisekoi:", popularity=200_000)
    assert _ranked([s1, s2], "Nisekoi:", 1)[0].id.anilist == 4


def test_a_synonym_match_ranks_below_a_real_title_match():
    """AniList synonyms are crowd-sourced aliases, not names."""
    from anime_sh.app.search import _rank_score, _norm, _squash

    by_title = _titled(1, "Gintama")
    by_synonym = _titled(2, "Something Else", synonyms=["Gintama"])
    nq, sq = _norm("Gintama"), _squash("Gintama")
    assert _rank_score(by_title, nq, sq) > _rank_score(by_synonym, nq, sq)
