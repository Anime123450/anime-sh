"""HiAnime provider: HTML parsing, the sub/dub split, and the candidate
pipeline — offline via an injected fake HTTP client.

The fixtures are trimmed from real hianime.at responses (Frieren, id 481)
rather than written from the docstring, because the parsing bugs worth catching
are the ones where the real markup differs from what the code expects.
"""

from __future__ import annotations

import base64

from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Audio,
    Episode,
    ProviderRef,
    Status,
    Title,
)
from anime_sh.providers.hianime.provider import (
    HianimeProvider,
    parse_episodes,
    parse_search,
    parse_servers,
)

SEARCH_HTML = """
<div class="flw-item flw-item-big">
  <div class="film-poster">
    <div class="tick ltr">
      <div class="tick-item tick-sub"><i class="fas fa-closed-captioning mr-1"></i>28</div>
      <div class="tick-item tick-dub"><i class="fas fa-microphone mr-1"></i>28</div>
      <div class="tick-item tick-eps">28</div>
    </div>
    <a href="https://hianime.at/watch/frieren-beyond-journeys-end-481"
       class="film-poster-ahref item-qtip"
       title="Frieren: Beyond Journey&#039;s End"
       aria-label="Watch Frieren: Beyond Journey&#039;s End"
       data-id="481"></a>
  </div>
  <div class="film-detail"><h3 class="film-name">
    <a href="https://hianime.at/frieren-beyond-journeys-end-481"
       class="dynamic-name" data-jname="Sousou no Frieren">Frieren</a>
  </h3></div>
</div>
<div class="flw-item flw-item-big">
  <div class="film-poster">
    <div class="tick ltr">
      <div class="tick-item tick-sub"><i class="fas fa-closed-captioning mr-1"></i>4</div>
      <div class="tick-item tick-eps">4</div>
    </div>
    <a href="https://hianime.at/watch/frieren-beyond-journeys-end-mini-anime-2851"
       class="film-poster-ahref item-qtip"
       title="Sousou no Frieren - Marumaru no Mahou (Mini Anime)"
       data-id="2851"></a>
  </div>
  <div class="film-detail"><h3 class="film-name">
    <a class="dynamic-name" data-jname="Sousou no Frieren Mini">Mini Anime</a>
  </h3></div>
</div>
"""

EPISODES_HTML = (
    '<div class="ss-list">'
    '<a title="The Journey&#039;s End" class="ssl-item ep-item" data-number="1" '
    'data-id="9227" href="https://hianime.at/watch/x-481?ep=9227"></a>'
    '<a title="It Didn&#039;t Have to Be Magic..." class="ssl-item ep-item" '
    'data-number="2" data-id="9228" href="https://hianime.at/watch/x-481?ep=9228"></a>'
    "</div>"
)


def _hash(url: str) -> str:
    return base64.b64encode(url.encode()).decode()


ZOKO_SUB = "https://zokoanime.video/stream/mal/52991/1/sub"
ZOKO_DUB = "https://zokoanime.video/stream/mal/52991/1/dub"
MEGA_SUB = "https://megaplay.buzz/stream/s-2/107257/sub"

SERVERS_HTML = (
    '<div class="ps_-block servers-sub"><div class="ps__-list">'
    f'<div class="item server-item" data-type="sub" data-server-name="ZokoAnime" '
    f'data-hash="{_hash(ZOKO_SUB)}"></div>'
    f'<div class="item server-item" data-type="sub" data-server-name="HD-1" '
    f'data-hash="{_hash(MEGA_SUB)}"></div>'
    '<div class="item server-item" data-type="sub" data-server-name="Broken" '
    'data-hash="!!!not base64!!!"></div>'
    '</div></div>'
    '<div class="ps_-block servers-dub"><div class="ps__-list">'
    f'<div class="item server-item" data-type="dub" data-server-name="ZokoAnime" '
    f'data-hash="{_hash(ZOKO_DUB)}"></div>'
    "</div></div>"
)


# -- parsing ---------------------------------------------------------------- #
def test_parse_search_reads_ids_titles_and_both_episode_counts():
    items = parse_search(SEARCH_HTML)
    assert [i["id"] for i in items] == ["481", "2851"]
    assert items[0]["title"] == "Frieren: Beyond Journey's End"
    assert items[0]["jp"] == "Sousou no Frieren"
    assert (items[0]["sub_eps"], items[0]["dub_eps"]) == (28, 28)
    # The mini entry has no dub tick at all — that is the signal find_sources
    # uses to skip it for a dub request without spending a round-trip.
    assert (items[1]["sub_eps"], items[1]["dub_eps"]) == (4, None)


def test_parse_episodes_keeps_number_id_and_title():
    eps = parse_episodes(EPISODES_HTML)
    assert [e["num"] for e in eps] == [1.0, 2.0]
    assert eps[0]["ep_id"] == "9227"
    assert eps[0]["title"] == "The Journey's End"


def test_parse_servers_decodes_the_embed_url():
    servers = parse_servers(SERVERS_HTML)
    subs = [s for s in servers if s["type"] == "sub"]
    assert [s["name"] for s in subs] == ["ZokoAnime", "HD-1"]
    assert subs[0]["url"] == ZOKO_SUB
    assert subs[1]["url"] == MEGA_SUB


def test_parse_servers_drops_an_undecodable_hash_and_keeps_the_rest():
    """One bad entry must not cost the episode its working servers."""
    names = [s["name"] for s in parse_servers(SERVERS_HTML)]
    assert "Broken" not in names
    assert "ZokoAnime" in names


# -- provider --------------------------------------------------------------- #
def _anime(status=Status.FINISHED, count=28) -> Anime:
    return Anime(
        id=AnimeId(anilist=154587),
        title=Title(
            romaji="Sousou no Frieren", english="Frieren: Beyond Journey's End"
        ),
        episode_count=count,
        status=status,
    )


class _Http:
    """Fake transport: answers by route, and records what was asked."""

    def __init__(self):
        self.calls: list[str] = []

    async def get_text(self, url, *, params=None, headers=None):
        self.calls.append(url)
        return SEARCH_HTML

    async def get_json(self, url, *, params=None, headers=None):
        self.calls.append(url)
        if "episode/list" in url:
            return {"status": True, "html": EPISODES_HTML}
        return {"status": True, "html": SERVERS_HTML}

    async def aclose(self):
        pass


async def test_find_sources_returns_matches_best_first():
    provider = HianimeProvider(http=_Http())
    sources = await provider.find_sources(_anime(), Audio.SUB)
    assert [s.anime_key for s in sources][0] == "481"
    assert sources[0].episode_count == 28
    assert all(s.provider == "hianime" for s in sources)


async def test_find_sources_skips_an_entry_with_no_dub():
    """The mini entry lists a sub count but no dub tick, so a dub request must
    not offer it — otherwise the fan-out spends a round-trip to learn nothing."""
    provider = HianimeProvider(http=_Http())
    sources = await provider.find_sources(_anime(), Audio.DUB)
    assert [s.anime_key for s in sources] == ["481"]
    assert sources[0].episode_count == 28


async def test_episodes_are_sorted_and_keyed_by_the_sites_episode_id():
    provider = HianimeProvider(http=_Http())
    ref = ProviderRef(provider="hianime", anime_key="481", audio=Audio.SUB)
    eps = await provider.episodes(ref, AnimeId(anilist=154587))
    assert [e.number for e in eps] == [1.0, 2.0]
    # Not the episode number: the servers endpoint only accepts the site's id.
    assert eps[0].episode_key == "9227"
    assert eps[0].title == "The Journey's End"


async def test_candidates_filters_to_the_requested_audio():
    provider = HianimeProvider(http=_Http())
    ref = ProviderRef(provider="hianime", anime_key="481", audio=Audio.DUB)
    episode = Episode(
        anime_id=AnimeId(anilist=154587),
        number=1.0,
        provider_ref=ref,
        episode_key="9227",
    )
    candidates = await provider.candidates(episode)
    assert [c.url for c in candidates] == [ZOKO_DUB]
    assert all(c.audio is Audio.DUB for c in candidates)


async def test_candidates_emit_every_server_even_ones_no_resolver_handles():
    """The megaplay servers here answer with an encrypted blob we do not
    decrypt. They are still emitted: which candidates are playable is the
    resolvers' judgement, and the fan-out is what makes an unplayable one
    harmless."""
    provider = HianimeProvider(http=_Http())
    ref = ProviderRef(provider="hianime", anime_key="481", audio=Audio.SUB)
    episode = Episode(
        anime_id=AnimeId(anilist=154587),
        number=1.0,
        provider_ref=ref,
        episode_key="9227",
    )
    hosts = [c.host for c in await provider.candidates(episode)]
    assert hosts == ["ZokoAnime", "HD-1"]
