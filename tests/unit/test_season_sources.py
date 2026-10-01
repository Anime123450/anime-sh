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


class SubtitledSequelProvider(SeasonySearchProvider):
    """The §9.2 case: a sequel marked by subtitle rather than by number.

    "Attack on Titan: Final Season", "JoJo's Bizarre Adventure: Stone Ocean" and
    "Demon Slayer: Entertainment District Arc" all read as season 1 — exactly
    like their prequels — so the season-number filter let them through and the
    sequel, ranked first, was picked as the source for its own prequel.
    """

    async def find_sources(self, anime, audio):
        return [
            SourceOption("fake", "final", "Attack on Titan: Final Season", 16, audio, 0.99),
            SourceOption("fake", "s1", "Attack on Titan", 25, audio, 0.90),
        ]


async def test_a_sequel_named_by_subtitle_is_not_a_source_for_its_prequel():
    mgr = ProviderManager([SubtitledSequelProvider()], match_timeout_s=5)

    refs = await mgr.resolve_sources(_show("Attack on Titan"), Audio.SUB)
    assert [r.anime_key for r in refs] == ["s1"], "the prequel got the sequel's entry"

    opts = await mgr.list_sources(_show("Attack on Titan"), Audio.SUB)
    assert [o.anime_key for o in opts] == ["s1"]


async def test_the_subtitled_sequel_still_finds_its_own_entry():
    """The filter has to cut both ways, or it has just moved the bug."""
    mgr = ProviderManager([SubtitledSequelProvider()], match_timeout_s=5)

    refs = await mgr.resolve_sources(_show("Attack on Titan: Final Season"), Audio.SUB)
    assert [r.anime_key for r in refs] == ["final"]


async def test_a_differently_worded_title_is_still_accepted():
    """The romaji title shares no words with the english one. Treating "differs"
    as "different entry" would reject every legitimate romaji match — a far
    wider bug than the one being fixed."""

    class RomajiOnly(SeasonySearchProvider):
        async def find_sources(self, anime, audio):
            return [SourceOption("fake", "jp", "Shingeki no Kyojin", 25, audio, 0.9)]

    mgr = ProviderManager([RomajiOnly()], match_timeout_s=5)
    opts = await mgr.list_sources(
        Anime(id=AnimeId(anilist=1),
              title=Title(romaji="Shingeki no Kyojin", english="Attack on Titan")),
        Audio.SUB,
    )
    assert [o.anime_key for o in opts] == ["jp"]


async def test_a_release_tag_is_not_a_subtitle():
    """"(Dub)" describes the release, not the work."""

    class Dubbed(SeasonySearchProvider):
        async def find_sources(self, anime, audio):
            return [SourceOption("fake", "dub", "Attack on Titan (Dub)", 25, audio, 0.9)]

    mgr = ProviderManager([Dubbed()], match_timeout_s=5)
    opts = await mgr.list_sources(_show("Attack on Titan"), Audio.SUB)
    assert [o.anime_key for o in opts] == ["dub"]


async def test_the_s2_shorthand_names_the_same_season_as_season_2():
    """`season_number` understood "… S2"; `_identity_words` did not strip it.

    So "Show S2" and "Show Season 2" agreed on the season and were then rejected
    as different *entries* -- `s2` survived as an identity word its twin did not
    carry. With the right source filtered out, `_best_ref` falls back to the
    unfiltered, similarity-ranked list, which is how a different season gets
    chosen for the one you opened.
    """

    class Shorthand(SeasonySearchProvider):
        async def find_sources(self, anime, audio):
            # Season 3 ranked first, as a provider's own similarity may well do.
            return [
                SourceOption("fake", "s3", "Full-Time Magister S3", 12, audio, 0.98),
                SourceOption("fake", "s2", "Full-Time Magister S2", 12, audio, 0.95),
            ]

    mgr = ProviderManager([Shorthand()], match_timeout_s=5)
    show = _show("Full-Time Magister Season 2")

    refs = await mgr.resolve_sources(show, Audio.SUB)
    assert [r.anime_key for r in refs] == ["s2"], "played a different season"

    opts = await mgr.list_sources(show, Audio.SUB)
    assert [o.anime_key for o in opts] == ["s2"]


async def test_the_padded_shorthand_works_too():
    """AniList writes both "S2" and "S02".

    A second, wrong-season option on purpose: the season filter falls back to the
    unfiltered list rather than return nothing, so a lone option comes back either
    way and a one-option case cannot tell the bug from the fix.
    """

    class Padded(SeasonySearchProvider):
        async def find_sources(self, anime, audio):
            return [
                SourceOption("fake", "s2", "Full-Time Magister S02", 12, audio, 0.9),
                SourceOption("fake", "s1", "Full-Time Magister", 12, audio, 0.8),
            ]

    mgr = ProviderManager([Padded()], match_timeout_s=5)
    opts = await mgr.list_sources(_show("Full-Time Magister 2nd Season"), Audio.SUB)
    assert [o.anime_key for o in opts] == ["s2"]


async def test_the_shorthand_still_cannot_stand_in_for_the_prequel():
    """The marker is now stripped from the identity words, so the only thing
    keeping the seasons apart is the season number. It has to be enough."""

    class Shorthand(SeasonySearchProvider):
        async def find_sources(self, anime, audio):
            return [
                SourceOption("fake", "s2", "Full-Time Magister S2", 12, audio, 0.99),
                SourceOption("fake", "s1", "Full-Time Magister", 12, audio, 0.90),
            ]

    mgr = ProviderManager([Shorthand()], match_timeout_s=5)
    refs = await mgr.resolve_sources(_show("Full-Time Magister"), Audio.SUB)
    assert [r.anime_key for r in refs] == ["s1"], "season 1 got the sequel"
