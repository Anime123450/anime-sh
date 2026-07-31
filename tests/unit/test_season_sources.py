"""A sequel must never be used as a source for its prequel (or vice versa).

Provider searches return neighbouring seasons, and a sequel's title is nearly
identical to its prequel's — so the top-ranked "match" for one season was
regularly the other one. Playing then streamed the wrong season while progress
was recorded against the season the user opened.
"""

from __future__ import annotations

from anime_sh.app.providers import ProviderManager
from anime_sh.domain.models import Anime, AnimeId, Audio, SourceOption, Title


class SeasonySearchProvider:
    """A provider whose search returns both seasons, sequel ranked first —
    exactly what the real ones do."""

    name = "fake"
    priority = 10
    api_version = 1

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        return [
            SourceOption("fake", "s2", "Bumpkin to Swordsman Season 2", 4, audio, 0.99),
            SourceOption("fake", "s1", "Bumpkin to Swordsman", 12, audio, 0.90),
        ]

    async def match(self, anime, audio):  # what the old code called
        return (await self.find_sources(anime, audio))[0].ref()

    async def episodes(self, ref, anime_id):
        return []

    async def candidates(self, episode):
        return []

    async def aclose(self):
        return None


def _show(title: str) -> Anime:
    return Anime(id=AnimeId(anilist=1), title=Title(romaji=title, english=title))


async def test_auto_match_picks_the_season_you_opened():
    """resolve_sources is the no-picker path — `anime play`, and the detail
    screen when no source is pinned."""
    mgr = ProviderManager([SeasonySearchProvider()], match_timeout_s=5)

    refs = await mgr.resolve_sources(_show("Bumpkin to Swordsman"), Audio.SUB)
    assert [r.anime_key for r in refs] == ["s1"], "season 1 got the sequel's entry"

    refs = await mgr.resolve_sources(_show("Bumpkin to Swordsman Season 2"), Audio.SUB)
    assert [r.anime_key for r in refs] == ["s2"]


async def test_source_picker_only_lists_this_season():
    mgr = ProviderManager([SeasonySearchProvider()], match_timeout_s=5)

    opts = await mgr.list_sources(_show("Bumpkin to Swordsman"), Audio.SUB)
    assert [o.anime_key for o in opts] == ["s1"]

    opts = await mgr.list_sources(_show("Bumpkin to Swordsman Season 2"), Audio.SUB)
    assert [o.anime_key for o in opts] == ["s2"]


async def test_falls_back_rather_than_leaving_a_show_unplayable():
    """If nothing matches the season, an imperfect source still beats none."""

    class OnlyOtherSeason(SeasonySearchProvider):
        async def find_sources(self, anime, audio):
            return [SourceOption("fake", "s3", "Bumpkin to Swordsman III", 6, audio, 0.9)]

    mgr = ProviderManager([OnlyOtherSeason()], match_timeout_s=5)
    opts = await mgr.list_sources(_show("Bumpkin to Swordsman"), Audio.SUB)
    assert [o.anime_key for o in opts] == ["s3"]
