"""The popularity index must not turn one bad minute into a bad session.

Fuzzy search ("fri" -> Frieren, "onepiece" -> One Piece) runs off a local
popularity index, built on first need from ten AniList catalog pages fetched at
once. AniList rate-limits below ten concurrent requests, so a failure there was
routine rather than exceptional — and two separate pieces of code treated that
routine failure as a permanent fact:

1. one failed page discarded the nine that had succeeded, and
2. the caller stored the resulting failure as "there is no index", which the
   `is not None` fast path then returned for the rest of the process.

Together they meant a single rate limit silently disabled fuzzy search until the
app was restarted.
"""

from __future__ import annotations

import asyncio

import pytest

from anime_sh.app.search import SearchService
from anime_sh.domain.models import Anime, AnimeId, Format, Title
from anime_sh.infra.metadata.anilist import AniListMetadata


def _anime(i: int) -> Anime:
    return Anime(id=AnimeId(anilist=i), title=Title(romaji=f"Show {i}"),
                 format=Format.TV)


class FlakyMetadata:
    """Empty search results (so the fallback path runs) and a `popular` that
    fails the first time it is asked."""

    name = "flaky"

    def __init__(self, fail_times: int = 1):
        self.popular_calls = 0
        self._fail_times = fail_times

    async def search(self, query, *, limit=20):
        return []

    async def popular(self, *, limit=500):
        self.popular_calls += 1
        if self.popular_calls <= self._fail_times:
            raise RuntimeError("AniList rate limited — try again in about 41s")
        return [_anime(i) for i in range(50)]


async def test_a_transient_index_failure_is_retried_not_remembered_forever():
    """The regression test for the session-long outage.

    The first build fails. Once the cooldown has passed, the next search must
    try again — before this, `_index` was set to `[]` and the fast path returned
    it unconditionally, so `popular` was never called a second time and only
    restarting the app restored fuzzy search.
    """
    metadata = FlakyMetadata()
    service = SearchService(metadata)

    await service.search("frieren")
    assert metadata.popular_calls == 1
    assert service._index is None, "a failure must not be stored as the index"

    service._index_failed_at = None  # stand in for the cooldown elapsing
    await service.search("frieren")

    assert metadata.popular_calls == 2, "the failure was never retried"
    assert service._index and len(service._index) == 50


async def test_a_failed_build_is_not_retried_on_every_keystroke():
    """The cooldown exists so a degraded search does not become a self-inflicted
    rate limit: each fallback search would otherwise re-fire ten catalog pages at
    a service already refusing us."""
    metadata = FlakyMetadata(fail_times=99)
    service = SearchService(metadata)

    for _ in range(5):
        await service.search("frieren")

    assert metadata.popular_calls == 1, (
        f"{metadata.popular_calls} rebuild attempts — the cooldown is not holding"
    )


async def test_a_permanently_absent_index_is_still_cached():
    """A metadata source with no `popular` at all is a real, permanent absence —
    unlike a failure, it should be settled once and never retried."""

    class NoPopular:
        name = "nopopular"

        async def search(self, query, *, limit=20):
            return []

    service = SearchService(NoPopular())
    await service.search("frieren")
    assert service._index == []


# --- the layer underneath -------------------------------------------------- #

def _page(page: int) -> dict:
    return {"Page": {"media": [
        {"id": page * 100 + j, "title": {"romaji": f"P{page}-{j}"},
         "format": "TV", "episodes": 12}
        for j in range(50)
    ]}}


async def test_one_failed_catalog_page_does_not_discard_the_others():
    """450 usable titles used to be thrown away because the tenth page 429'd."""
    metadata = AniListMetadata()
    seen: list[int] = []

    async def fake_query(query, variables):
        seen.append(variables["page"])
        if variables["page"] == 7:
            raise RuntimeError("rate limited on page 7")
        return _page(variables["page"])

    metadata._query = fake_query
    try:
        got = await metadata.popular(limit=500)
    finally:
        await metadata.aclose()

    assert len(seen) == 10
    assert len(got) == 450, f"kept {len(got)} of the 450 titles that arrived"


async def test_a_catalog_where_every_page_fails_is_a_real_failure():
    """Distinct from a thin answer: the caller has to be able to tell the
    difference so it knows to retry later."""
    metadata = AniListMetadata()

    async def all_fail(query, variables):
        raise RuntimeError("rate limited")

    metadata._query = all_fail
    try:
        with pytest.raises(Exception):
            await metadata.popular(limit=500)
    finally:
        await metadata.aclose()
