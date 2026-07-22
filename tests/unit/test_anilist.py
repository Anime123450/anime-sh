"""AniList metadata source — request-shape regressions, offline."""

from __future__ import annotations

from anime_sh.domain.models import AnimeId
from anime_sh.infra.metadata.anilist import AniListMetadata


class _FakeHttp:
    def __init__(self, page=False):
        self.sent = None
        self._page = page

    def __init__(self, page=False, response=None):
        self.sent = None
        self._page = page
        self._response = response

    async def post_json(self, url, *, json=None, headers=None):
        self.sent = json
        if self._response is not None:
            return self._response
        if self._page:
            return {"data": {"Page": {"media": [{"id": 1, "title": {"romaji": "A"}}]}}}
        return {"data": {"Media": {"id": 196187, "title": {"romaji": "X"}}}}


async def test_get_omits_null_ids():
    # An explicit {"malId": null} makes AniList 404 even with a valid id —
    # missing ids must be omitted from the variables entirely.
    http = _FakeHttp()
    meta = AniListMetadata(http=http)
    anime = await meta.get(AnimeId(anilist=196187))
    assert anime.id.anilist == 196187
    assert http.sent["variables"] == {"id": 196187}


async def test_get_passes_mal_id_when_present():
    http = _FakeHttp()
    meta = AniListMetadata(http=http)
    await meta.get(AnimeId(anilist=196187, mal=62076))
    assert http.sent["variables"] == {"id": 196187, "malId": 62076}


async def test_search_filtered_builds_variables_and_maps_sort():
    http = _FakeHttp(page=True)
    meta = AniListMetadata(http=http)
    out = await meta.search_filtered(
        genres=["action", "comedy"], year=2024, format="tv",
        status="releasing", sort="score", limit=5,
    )
    v = http.sent["variables"]
    assert v["genres"] == ["Action", "Comedy"]  # title-cased for AniList
    assert v["year"] == 2024 and v["format"] == "TV" and v["status"] == "RELEASING"
    assert v["sort"] == ["SCORE_DESC"] and v["perPage"] == 5
    assert "search" not in v  # omitted when None
    assert out and out[0].id.anilist == 1


async def test_sequel_returns_first_anime_sequel_edge():
    resp = {"data": {"Media": {"relations": {"edges": [
        {"relationType": "PREQUEL", "node": {"type": "ANIME", "id": 9,
         "title": {"romaji": "Prev"}}},
        {"relationType": "SEQUEL", "node": {"type": "MANGA", "id": 8,
         "title": {"romaji": "Manga"}}},  # wrong media type, skip
        {"relationType": "SEQUEL", "node": {"type": "ANIME", "id": 7,
         "title": {"romaji": "Season 2"}}},
    ]}}}}
    meta = AniListMetadata(http=_FakeHttp(response=resp))
    seq = await meta.sequel(AnimeId(anilist=1))
    assert seq is not None and seq.id.anilist == 7


async def test_sequel_none_when_no_sequel():
    resp = {"data": {"Media": {"relations": {"edges": []}}}}
    meta = AniListMetadata(http=_FakeHttp(response=resp))
    assert await meta.sequel(AnimeId(anilist=1)) is None


async def test_search_filtered_defaults_sort_by_context():
    # No query, no sort → browse by popularity; a query → relevance.
    http = _FakeHttp(page=True)
    meta = AniListMetadata(http=http)
    await meta.search_filtered(year=2020)
    assert http.sent["variables"]["sort"] == ["POPULARITY_DESC"]
    await meta.search_filtered("frieren")
    assert http.sent["variables"]["sort"] == ["SEARCH_MATCH"]
