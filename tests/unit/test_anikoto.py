"""Anikoto provider: HTML parsing (search / episodes / servers), matching, and
the candidate pipeline — offline via an injected fake HTTP client. Fixtures
mirror the real anikototv.to markup."""

from __future__ import annotations

import pytest

from anime_sh.domain.models import Anime, AnimeId, Audio, Episode, ProviderRef, Title
from anime_sh.providers.anikoto.provider import (
    AnikotoProvider,
    _best_match,
    parse_episodes,
    parse_search,
    parse_servers,
)

SEARCH_HTML = '''
<div id="list-items" class="ani items">
  <div class="item "><div class="inner">
    <div class="ani poster tip" data-tip="8950">
      <span class="ep-status sub"><span> 2</span></span>
      <a href="https://anikototv.to/watch/smoking-e086a/ep-1"><img/></a></div>
    <div class="info"><div class="b1">
      <a class="name d-title" href="https://anikototv.to/watch/smoking-e086a/ep-1"
         data-jp="Super no Ura de Yani Suu Futari">Smoking Behind the Supermarket with You</a>
    </div></div></div></div>
  <div class="item "><div class="inner">
    <div class="ani poster tip" data-tip="8851">
      <span class="ep-status sub"><span> 12</span></span>
      <a href="https://anikototv.to/watch/smoking-mini/ep-1"><img/></a></div>
    <div class="info"><div class="b1">
      <a class="name d-title" href="https://anikototv.to/watch/smoking-mini/ep-1"
         data-jp="[Mini] Super no Ura de Yani Suu Futari">[Mini] Smoking Behind the Supermarket with You</a>
    </div></div></div></div>
</div>
'''

EPISODES_HTML = (
    '<ul><li><a href="#" data-id="97908" data-num="1" data-slug="1" data-mal="52076" '
    'data-sub="1" data-dub="1" data-ids="SERVERTOKEN1">Ep 1</a></li>'
    '<li><a href="#" data-id="97909" data-num="2" data-mal="52076" '
    'data-sub="1" data-dub="0" data-ids="SERVERTOKEN2">Ep 2</a></li></ul>'
)

SERVERS_HTML = (
    '<div class="servers"><div class="type" data-type="sub">'
    '<ul><li data-ep-id="97908" data-sv-id="8e4" data-link-id="LINK_A">VidPlay-1</li>'
    '<li data-ep-id="97908" data-sv-id="323" data-link-id="LINK_B">HD-1</li></ul></div>'
    '<div class="type" data-type="dub">'
    '<ul><li data-ep-id="97908" data-sv-id="9f2" data-link-id="LINK_C">VidPlay-1</li></ul></div></div>'
)


# -- parsing ---------------------------------------------------------------- #
def test_parse_search():
    items = parse_search(SEARCH_HTML)
    assert len(items) == 2
    assert items[0]["id"] == "8950"
    assert items[0]["title"] == "Smoking Behind the Supermarket with You"
    assert items[0]["jp"] == "Super no Ura de Yani Suu Futari"
    assert items[0]["sub_eps"] == 2
    assert items[1]["sub_eps"] == 12


def test_parse_episodes():
    eps = parse_episodes(EPISODES_HTML)
    assert [e["num"] for e in eps] == [1.0, 2.0]
    assert eps[0]["ids"] == "SERVERTOKEN1"
    assert eps[0]["sub"] and eps[0]["dub"]
    assert eps[1]["dub"] is False


def test_parse_servers_groups_by_type():
    servers = parse_servers(SERVERS_HTML)
    subs = [s for s in servers if s["type"] == "sub"]
    dubs = [s for s in servers if s["type"] == "dub"]
    assert [s["name"] for s in subs] == ["VidPlay-1", "HD-1"]
    assert subs[0]["link_id"] == "LINK_A"
    assert len(dubs) == 1


def test_best_match_prefers_entry_matching_episode_count():
    # AniList says 12 eps. Two same-named entries: the TV one has only 2 aired,
    # the "[Mini]" batch has all 12 — pick the complete one so the whole run is
    # watchable, even though the TV title is a marginally closer string match.
    anime = Anime(
        id=AnimeId(anilist=196187),
        title=Title(romaji="Super no Ura de Yani Suu Futari",
                    english="Smoking Behind the Supermarket with You"),
        episode_count=12,
    )
    best = _best_match(anime, parse_search(SEARCH_HTML))
    assert best is not None and best["id"] == "8851"  # the 12-episode entry


def test_best_match_uses_title_when_no_episode_count():
    # With no planned count, fall back to the closest title (the exact TV name).
    anime = Anime(
        id=AnimeId(anilist=1),
        title=Title(romaji="Super no Ura de Yani Suu Futari",
                    english="Smoking Behind the Supermarket with You"),
    )
    best = _best_match(anime, parse_search(SEARCH_HTML))
    # No target count → prefer the most complete (most episodes) among matches.
    assert best is not None and best["id"] == "8851"


async def test_find_sources_returns_all_matches_best_first():
    class _Http:
        async def get_text(self, url, *, params=None, headers=None):
            return SEARCH_HTML

    provider = AnikotoProvider(http=_Http())
    anime = Anime(
        id=AnimeId(anilist=196187), episode_count=12,
        title=Title(romaji="Super no Ura de Yani Suu Futari",
                    english="Smoking Behind the Supermarket with You"),
    )
    sources = await provider.find_sources(anime, Audio.SUB)
    # Both entries surface; the 12-episode one is first (matches planned count).
    assert [s.anime_key for s in sources] == ["8851", "8950"]
    assert sources[0].episode_count == 12 and sources[0].provider == "anikoto"


# -- provider over fake HTTP ------------------------------------------------ #
class FakeHttp:
    def __init__(self):
        self.calls = []

    async def get_text(self, url, *, params=None, headers=None):
        self.calls.append(("GET-text", url, params))
        return SEARCH_HTML

    async def get_json(self, url, *, params=None, headers=None):
        self.calls.append(("GET-json", url, params))
        if "/episode/list/" in url:
            return {"status": 200, "result": EPISODES_HTML}
        if "/server/list" in url:
            return {"status": 200, "result": SERVERS_HTML}
        if url.endswith("/ajax/server"):
            # one embed URL per link id
            return {"status": 200, "result": {"url": f"https://megaplay.buzz/stream/s-5/{params['get']}/sub"}}
        raise AssertionError(url)


async def test_match_and_episodes_and_candidates():
    http = FakeHttp()
    provider = AnikotoProvider(http=http)
    anime = Anime(
        id=AnimeId(anilist=196187),
        title=Title(romaji="Super no Ura de Yani Suu Futari",
                    english="Smoking Behind the Supermarket with You"),
    )
    ref = await provider.match(anime, Audio.SUB)
    assert ref is not None and ref.anime_key == "8851"  # the complete entry

    eps = await provider.episodes(ref, anime.id)
    assert [e.number for e in eps] == [1.0, 2.0]
    assert eps[0].episode_key == "SERVERTOKEN1"  # carries the server token

    cands = await provider.candidates(eps[0])
    # sub episode -> only the two sub servers, each resolved to an embed URL
    assert [c.host for c in cands] == ["VidPlay-1", "HD-1"]
    assert cands[0].url == "https://megaplay.buzz/stream/s-5/LINK_A/sub"


async def test_dub_episode_filtered_out_when_only_sub():
    provider = AnikotoProvider(http=FakeHttp())
    anime = Anime(id=AnimeId(anilist=1), title=Title(romaji="Super no Ura de Yani Suu Futari"))
    ref = ProviderRef(provider="anikoto", anime_key="8950", audio=Audio.DUB)
    eps = await provider.episodes(ref, anime.id)
    # Ep 2 is sub-only (data-dub=0), so a DUB request drops it.
    assert [e.number for e in eps] == [1.0]
