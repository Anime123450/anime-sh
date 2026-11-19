"""AnimeGG provider + resolver: HTML parsing, sub/dub handling and the
candidate → stream pipeline, offline via an injected fake HTTP client. Fixtures
are trimmed copies of the real animegg.org markup."""

from __future__ import annotations

from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Audio,
    ProviderRef,
    Quality,
    StreamCandidate,
    StreamKind,
    Title,
)
from anime_sh.domain.ports import Provider, Resolver
from anime_sh.providers.animegg import AnimeggProvider
from anime_sh.providers.animegg.provider import (
    parse_embeds,
    parse_episodes,
    parse_search,
)
from anime_sh.resolvers.animegg import AnimeggResolver
from anime_sh.resolvers.animegg.resolver import parse_sources

SEARCH_HTML = """
<div class="moose page"><a href="/series/sousou-no-frieren" class="mse">
  <div class="media searchre"><img src="https://vidcache.net:8161/x/jpeg" class="media-object">
    <div class="media-body"><div class="first">
      <h2>Sousou no Frieren</h2>
      <p class="infoami">
        <div>Episodes: 25</div>
        <div>Alt Titles : 葬送のフリーレン; Frieren: Beyond Journey's End</div>
        <div>Status : Ongoing</div>
      </p></div></div></div></a><a href="/series/sousou-no-frieren-no-mahou" class="mse">
  <div class="media searchre"><div class="media-body"><div class="first">
      <h2>Sousou no Frieren: ●● no Mahou</h2>
      <p class="infoami"><div>Episodes: 10</div><div>Status : Ongoing</div></p>
  </div></div></div></a><a href="/series/frieren-beyond-journeys-end-season-2" class="mse">
  <div class="media searchre"><div class="media-body"><div class="first">
      <h2>Frieren beyond journeys end season 2</h2>
      <p class="infoami"><div>Episodes: 10</div></p>
  </div></div></div></a>
</div>
"""

SERIES_HTML = """
<ul class="newmanga"><li>
<div><a href="/sousou-no-frieren-episode-3" class="anm_det_pop"><strong>Sousou no Frieren 3</strong></a><i class="anititle">Episode 3</i><a href="/sousou-no-frieren-episode-3#subbed"><span class="btn-xs btn-subbed">SUBBED</span></a><a href="/sousou-no-frieren-episode-3#dubbed"><span class="btn-xs btn-dubbed">DUBBED</span></a></div>
<div><a href="/sousou-no-frieren-episode-2" class="anm_det_pop"><strong>Sousou no Frieren 2</strong></a><i class="anititle">Episode 2</i><a href="/sousou-no-frieren-episode-2#subbed"><span class="btn-xs btn-subbed">SUBBED</span></a><a href="/sousou-no-frieren-episode-2#dubbed"><span class="btn-xs btn-dubbed">DUBBED</span></a></div>
<div><a href="/sousou-no-frieren-episode-1" class="anm_det_pop"><strong>Sousou no Frieren 1</strong></a><i class="anititle">Episode 1</i><a href="/sousou-no-frieren-episode-1#subbed"><span class="btn-xs btn-subbed">SUBBED</span></a><a href="/sousou-no-frieren-episode-1#dubbed"><span class="btn-xs btn-dubbed">DUBBED</span></a></div>
<div><a href="/sousou-no-frieren-episode-4" class="anm_det_pop"><strong>Sousou no Frieren 4</strong></a><i class="anititle">Episode 4</i><a href="/sousou-no-frieren-episode-4#subbed"><span class="btn-xs btn-subbed">SUBBED</span></a></div>
<div><a href="/sousou-no-frieren-episode-5" class="anm_det_pop"><strong>Sousou no Frieren 5</strong></a><i class="anititle">Episode 5</i></div>
<div><a href="/other-show-episode-9#subbed">unrelated</a></div>
</li></ul>
"""

EPISODE_HTML = """
<ul class="nav nav-tabs">
<li><a href="#subbed-Animegg" data-toggle="tab" data-id='147208' data-mirror="Animegg" data-version="subbed"><span>SUBBED</span></a></li>
<li><a href="#dubbed-Animegg" data-toggle="tab" data-id='147209' data-mirror="Animegg" data-version="dubbed"><span>DUBBED</span></a></li>
</ul>
<div class="tab-content embed-responsive embed-responsive-16by9">
  <div id="subbed-Animegg" class="tab-pane">
    <iframe src="/embed/147208" allowfullscreen style="border: 0;" scrolling="no" class="video"></iframe>
  </div>
  <div id="dubbed-Animegg" class="tab-pane">
    <iframe src="/embed/147209" allowfullscreen style="border: 0;" scrolling="no" class="video"></iframe>
  </div>
</div>
"""

SUB_ONLY_EPISODE_HTML = EPISODE_HTML.split('<div id="dubbed-Animegg"')[0] + "</div>"

EMBED_HTML = """
<script>
  var videoSources = [{file: "/play/550902/video.mp4?for=101", label: "480p", bk: "aHR0", isBk: false },{file: "/play/550904/video.mp4?for=101", label: "720p", bk: "aHR0", isBk: false },{file: "/play/550908/video.mp4?for=101", label: "1080p", bk: "aHR0", isBk: false }];
  jwplayer("player").setup({ sources: videoSources });
</script>
"""


# -- pure parsing ----------------------------------------------------------- #
def test_parse_search_cards():
    items = parse_search(SEARCH_HTML)
    assert [(i["slug"], i["title"], i["sub_eps"]) for i in items] == [
        ("sousou-no-frieren", "Sousou no Frieren", 25),
        ("sousou-no-frieren-no-mahou", "Sousou no Frieren: ●● no Mahou", 10),
        ("frieren-beyond-journeys-end-season-2",
         "Frieren beyond journeys end season 2", 10),
    ]
    assert "Frieren: Beyond Journey's End" in items[0]["alts"]
    assert items[1]["alts"] == []


def test_parse_episodes_flags_audio_and_scopes_to_slug():
    eps = parse_episodes(SERIES_HTML, "sousou-no-frieren")
    assert [e["number"] for e in eps] == [1.0, 2.0, 3.0, 4.0, 5.0]  # not other-show 9
    by_num = {e["number"]: e for e in eps}
    assert by_num[1.0] == {"number": 1.0, "sub": True, "dub": True}
    assert by_num[4.0] == {"number": 4.0, "sub": True, "dub": False}
    # a row with no audio marker defaults to sub
    assert by_num[5.0] == {"number": 5.0, "sub": True, "dub": False}


def test_parse_embeds_separates_sub_and_dub():
    assert parse_embeds(EPISODE_HTML, "subbed") == ["https://www.animegg.org/embed/147208"]
    assert parse_embeds(EPISODE_HTML, "dubbed") == ["https://www.animegg.org/embed/147209"]
    assert parse_embeds(SUB_ONLY_EPISODE_HTML, "dubbed") == []
    assert parse_embeds("<p>nothing</p>", "subbed") == []


def test_parse_sources_one_stream_per_quality_best_first():
    embed = "https://www.animegg.org/embed/147208"
    streams = parse_sources(EMBED_HTML, embed)
    assert [s.quality for s in streams] == [Quality.Q1080, Quality.Q720, Quality.Q480]
    assert streams[1].url == "https://www.animegg.org/play/550904/video.mp4?for=101"
    for s in streams:
        assert s.kind is StreamKind.MP4
        assert s.headers["Referer"] == embed
    assert parse_sources("<p>no player</p>", embed) == []


# -- provider over a fake HTTP ---------------------------------------------- #
class _FakeHttp:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[tuple[str, dict | None, dict | None]] = []

    async def get_text(self, url, *, params=None, headers=None):
        self.calls.append((url, params, headers))
        if url.endswith("/search"):
            return self.pages["search"]
        for key in sorted((k for k in self.pages if k != "search"), key=len, reverse=True):
            if url.endswith(key):
                return self.pages[key]
        raise AssertionError(f"unexpected fetch: {url} {params}")

    async def aclose(self):
        pass


def _anime(**kw):
    return Anime(
        id=AnimeId(anilist=154587),
        title=Title(romaji="Sousou no Frieren",
                    english="Frieren: Beyond Journey's End"),
        episode_count=28, **kw,
    )


def _pages():
    return {
        "search": SEARCH_HTML,
        "/series/sousou-no-frieren": SERIES_HTML,
        "/series/sousou-no-frieren-no-mahou": "<p>no episodes here</p>",
        "/series/frieren-beyond-journeys-end-season-2": "<p>none</p>",
        "/sousou-no-frieren-episode-1": EPISODE_HTML,
    }


def test_provider_satisfies_ports():
    prov = AnimeggProvider(http=_FakeHttp({}))
    assert isinstance(prov, Provider)
    assert prov.name == "animegg" and prov.priority == 60 and prov.api_version == 1
    assert isinstance(AnimeggResolver(http=_FakeHttp({})), Resolver)


async def test_find_sources_picks_the_main_series_first():
    prov = AnimeggProvider(http=_FakeHttp(_pages()))
    sources = await prov.find_sources(_anime(), Audio.SUB)
    assert sources[0].anime_key == "sousou-no-frieren"
    assert sources[0].provider == "animegg" and sources[0].audio is Audio.SUB
    assert sources[0].confidence == 1.0  # exact alt-title match
    assert (await prov.match(_anime(), Audio.SUB)).anime_key == "sousou-no-frieren"


async def test_dub_sources_require_an_actual_dub():
    prov = AnimeggProvider(http=_FakeHttp(_pages()))
    sources = await prov.find_sources(_anime(), Audio.DUB)
    assert [s.anime_key for s in sources] == ["sousou-no-frieren"]  # others have no dub
    assert sources[0].audio is Audio.DUB

    pages = _pages() | {"/series/sousou-no-frieren": SERIES_HTML.replace("#dubbed", "#x")}
    assert await AnimeggProvider(http=_FakeHttp(pages)).match(_anime(), Audio.DUB) is None


async def test_episodes_filtered_by_audio():
    prov = AnimeggProvider(http=_FakeHttp(_pages()))
    aid = AnimeId(anilist=154587)
    sub = await prov.episodes(ProviderRef("animegg", "sousou-no-frieren", Audio.SUB), aid)
    dub = await prov.episodes(ProviderRef("animegg", "sousou-no-frieren", Audio.DUB), aid)
    assert [e.number for e in sub] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert [e.number for e in dub] == [1.0, 2.0, 3.0]
    assert sub[0].episode_key == "1" and sub[0].anime_id == aid


async def test_candidates_sub_vs_dub():
    http = _FakeHttp(_pages())
    prov = AnimeggProvider(http=http)
    aid = AnimeId(anilist=154587)
    sub_ref = ProviderRef("animegg", "sousou-no-frieren", Audio.SUB)
    dub_ref = ProviderRef("animegg", "sousou-no-frieren", Audio.DUB)

    sub_ep = (await prov.episodes(sub_ref, aid))[0]
    (c,) = await prov.candidates(sub_ep)
    assert c.host == "animegg" and c.audio is Audio.SUB
    assert c.url == "https://www.animegg.org/embed/147208"
    assert c.headers["Referer"] == "https://www.animegg.org/sousou-no-frieren-episode-1"

    dub_ep = (await prov.episodes(dub_ref, aid))[0]
    (c,) = await prov.candidates(dub_ep)
    assert c.audio is Audio.DUB and c.url == "https://www.animegg.org/embed/147209"


async def test_search_queries_use_q_param():
    http = _FakeHttp(_pages())
    await AnimeggProvider(http=http).find_sources(_anime(), Audio.SUB)
    url, params, _ = http.calls[0]
    assert url == "https://www.animegg.org/search" and params == {"q": "Sousou no Frieren"}


# -- resolver --------------------------------------------------------------- #
async def test_resolver_pipeline():
    http = _FakeHttp({"/embed/147208": EMBED_HTML})
    res = AnimeggResolver(http=http)
    cand = StreamCandidate(
        host="animegg", url="https://www.animegg.org/embed/147208",
        headers={"Referer": "https://www.animegg.org/sousou-no-frieren-episode-1"},
    )
    assert res.handles(cand)
    assert not res.handles(StreamCandidate(host="x", url="https://other.example/embed/1"))
    streams = await res.resolve(cand)
    assert [s.quality for s in streams] == [Quality.Q1080, Quality.Q720, Quality.Q480]
    assert all(s.headers["Referer"] == cand.url for s in streams)
    # the embed page is fetched with the episode page as Referer
    assert http.calls[0][2] == {"Referer": "https://www.animegg.org/sousou-no-frieren-episode-1"}


async def test_resolver_raises_when_no_sources():
    import pytest

    from anime_sh.domain.errors import ResolverError

    res = AnimeggResolver(http=_FakeHttp({"/embed/9": "<p>gone</p>"}))
    with pytest.raises(ResolverError):
        await res.resolve(StreamCandidate(host="animegg", url="https://www.animegg.org/embed/9"))
