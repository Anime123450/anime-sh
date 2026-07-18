"""AniList metadata source — request-shape regressions, offline."""

from __future__ import annotations

from anime_sh.domain.models import AnimeId
from anime_sh.infra.metadata.anilist import AniListMetadata


class _FakeHttp:
    def __init__(self):
        self.sent = None

    async def post_json(self, url, *, json=None, headers=None):
        self.sent = json
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
