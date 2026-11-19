"""Blogger resolver + the animepahe.ch (WordPress/dramastream) provider.

Offline: fixtures mirror the real markup (search cards, ``div.eplister``, the
``player-embed`` iframe, and Blogger's multi-layer-escaped player config).
"""

from __future__ import annotations

import pytest

from anime_sh.domain.errors import ResolverError
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
from anime_sh.infra.http import HttpError
from anime_sh.providers.blogger_sites import AnimepaheChProvider, ZorotvProvider
from anime_sh.providers.blogger_sites.provider import (
    _clean_query,
    parse_embed_iframes,
    parse_episodes,
    parse_search,
)
from anime_sh.resolvers.blogger import BloggerResolver
from anime_sh.resolvers.blogger.resolver import (
    parse_video_urls,
    streams_from_page,
)

BASE = "https://animepahe.ch"
IFRAME = "https://www.blogger.com/video.g?token=AD6v5dzSQYm8Z6&origin=op.blogspot.com"

SEARCH_HTML = """
<div class="listupd"><article class="bs" itemscope="itemscope">
 <div class="bsx"><a href="https://animepahe.ch/series/overgeared/" itemprop="url" title="Overgeared" class="tip" rel="20178">
  <div class="bt"><span class="epx">Ongoing</span><span class="sb Sub">Sub</span></div>
  <div class="tt tts">Overgeared<h2 itemprop="headline">Overgeared</h2></div></a></div></article>
<article class="bs"><div class="bsx"><a href="https://animepahe.ch/series/sousou-no-frieren/" title="Frieren: Beyond Journey&#039;s End" class="tip">
  <div class="tt">x</div></a></div></article>
<article class="bs"><div class="bsx"><a href="https://animepahe.ch/series/overgeared/" title="Overgeared dup"></a></div></article>
</div>
"""

SERIES_HTML = """
<div class="lastend"><a href="https://animepahe.ch/overgeared-episode-1-english-subbed/"><span class="epcur epcurfirst">Episode 1</span></a>
<a href="https://animepahe.ch/overgeared-episode-3-english-subbed/"><span class="epcur epcurlast">Episode 3</span></a></div>
<div class="eplister"><ul>
<li data-index="0"><a href="https://animepahe.ch/overgeared-episode-3-english-subbed/">
  <div class="epl-num">3</div><div class="epl-title">Overgeared Episode 3 English Subbed</div></a></li>
<li data-index="1"><a href="https://animepahe.ch/overgeared-episode-2-5-english-subbed/">
  <div class="epl-num">2.5</div></a></li>
<li data-index="2"><a href="https://animepahe.ch/overgeared-episode-1-english-subbed/">
  <div class="epl-num">1</div></a></li>
</ul></div>
<ul><li><a href="https://animepahe.ch/other-show-episode-9-english-subbed/"><div class="epl-num">9</div></a></li></ul>
"""

EPISODE_HTML = f"""
<div class="player-embed" id="pembed">
  <iframe src="{IFRAME.replace('&', '&amp;')}" width="100%" height="266" allowfullscreen></iframe>
</div>
"""

# Blogger's real page nests the config as an HTML-attribute-escaped JSON blob:
# &quot; entities, \\u003d / \\u0026 escapes (doubled backslashes), repeated per
# format, plus a SABR streaming URL that has no itag.
def _gv(itag: int | None, sig: str) -> str:
    q = ["expire\\\\u003d1795093968", "ip\\\\u003d1.2.3.4", "id\\\\u003dabc"]
    if itag:
        q.append(f"itag\\\\u003d{itag}")
    q += ["source\\\\u003dblogger", f"sig\\\\u003d{sig}\\\\u003d"]
    return "https://rr1---sn-x.googlevideo.com/videoplayback?" + "\\\\u0026".join(q)


BLOGGER_HTML = (
    "<html><script>var x=1;</script><div data-v=\"[[[&quot;"
    + _gv(18, "SIG18") + "&quot;,[18]],[&quot;" + _gv(22, "SIG22") + "&quot;,[22]]]\">"
    "</div><div data-w=\"{\\\\&quot;url\\\\&quot;:\\\\&quot;" + _gv(18, "SIG18B")
    + "\\\\&quot;,\\\\&quot;sabr\\\\&quot;:\\\\&quot;" + _gv(None, "SABR") + "\\\\&quot;}\"></div></html>"
)


# The zorotv wrapper renders the URL into plain JS (literal & and =), once as a
# string and once inside a JSON array of sources; the SABR source has no itag.
_PLAIN = ("https://rr1---sn-x.googlevideo.com/videoplayback?expire=1795093935&ip=38.1.2.3"
          "&id=57&itag=18&source=blogger&mime=video/mp4&sig=AE0s2JY=&lsig=APaT==")
_SABR = "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1&id=57&source=blogger&sabr=1"
WRAPPER_PAGE = (
    f'<script>var fileUrl = "{_PLAIN}";\n'
    f'var sources = [{{"file":"{_PLAIN}","type":"mp4","label":"360p","default":true}},'
    f'{{"file":"{_SABR}","type":"mp4"}}];</script>'
)


# -- provider parsing ------------------------------------------------------- #
def test_parse_search_slug_title_dedup_and_entities():
    assert parse_search(SEARCH_HTML) == [
        {"id": "overgeared", "title": "Overgeared"},
        {"id": "sousou-no-frieren", "title": "Frieren: Beyond Journey's End"},
    ]


def test_parse_episodes_scoped_to_eplister_with_decimals():
    assert parse_episodes(SERIES_HTML) == [
        (1.0, "overgeared-episode-1-english-subbed"),
        (2.5, "overgeared-episode-2-5-english-subbed"),
        (3.0, "overgeared-episode-3-english-subbed"),
    ]  # not other-show-9


def test_parse_blogger_iframe_unescapes_entities():
    assert parse_embed_iframes(EPISODE_HTML) == [IFRAME]
    assert parse_embed_iframes("<p>nothing</p>") == []


WRAPPER = ("https://zorotv.ba/wp-content/themes/animestream-child/player/"
           "?source=blogger&url=QUJD%3D")
WRAPPER_EPISODE_HTML = (
    '<div class="player-embed" id="pembed"><iframe width="100%" height="100%" '
    'src="https://zorotv.ba/wp-content/themes/animestream-child/player/'
    '?source=blogger&#038;url=QUJD%3D" frameborder="0"></iframe></div>')


def test_clean_query_strips_punctuation_the_sites_choke_on():
    assert _clean_query("Frieren: Beyond Journey's End") == "Frieren Beyond Journey s End"
    assert _clean_query("Re:ZERO -Starting Life-") == "Re ZERO Starting Life"
    assert _clean_query("!!!") == ""


def test_mirror_dropdown_blogger_server_is_found():
    import base64
    snippet = f'<iframe src="{IFRAME}" width="100%"></iframe>'
    other = '<iframe src="https://megaplay.buzz/stream/s-2/1/sub"></iframe>'
    opt = lambda x: f'<option value="{base64.b64encode(x.encode()).decode()}">S</option>'
    html = (other.replace("<iframe", '<div class="player-embed"><iframe') + "</div>"
            '<select class="mirror"><option value="">Select Video Server</option>'
            + opt(other) + opt(snippet) + "</select>")
    assert parse_embed_iframes(html) == [IFRAME]


def test_zorotv_non_blogger_source_is_not_a_candidate():
    # Some zorotv titles use source=embed (a third-party host) — not ours.
    html = ('<div class="player-embed"><iframe src="https://zorotv.ba/wp-content/themes/'
            'animestream-child/player/?source=embed&#038;url=QUJD"></iframe></div>')
    assert parse_embed_iframes(html) == []


def test_zorotv_wrapper_iframe_is_a_candidate():
    assert parse_embed_iframes(WRAPPER_EPISODE_HTML) == [WRAPPER]
    # other iframes (ads, other sources) are ignored
    other = '<div class="player-embed"><iframe src="https://ads.example/x"></iframe></div>'
    assert parse_embed_iframes(other) == []


# -- resolver --------------------------------------------------------------- #
def test_parse_video_urls_unescapes_every_layer_and_skips_itagless():
    urls = parse_video_urls(BLOGGER_HTML)
    assert set(urls) == {18, 22}  # SABR url (no itag) ignored
    u = urls[22]
    assert u.startswith("https://rr1---sn-x.googlevideo.com/videoplayback?expire=1795093968&ip=1.2.3.4")
    assert "itag=22" in u and "&source=blogger&sig=SIG22=" in u
    assert "\\" not in u and "&quot;" not in u and "u003d" not in u
    assert urls[18].endswith("sig=SIG18=")  # first occurrence wins over SIG18B


def test_streams_ordered_best_first_with_itag_quality():
    streams = streams_from_page(BLOGGER_HTML)
    assert [(s.quality, s.kind) for s in streams] == [
        (Quality.Q720, StreamKind.MP4), (Quality.Q360, StreamKind.MP4)]
    assert "itag=22" in streams[0].url and "itag=18" in streams[1].url


def test_unmapped_itags_are_dropped():
    page = BLOGGER_HTML.replace("itag\\\\u003d22", "itag\\\\u003d394")
    assert [s.quality for s in streams_from_page(page)] == [Quality.Q360]


def test_wrapper_page_yields_360p_and_skips_sabr():
    urls = parse_video_urls(WRAPPER_PAGE)
    assert list(urls) == [18] and urls[18] == _PLAIN
    assert [s.quality for s in streams_from_page(WRAPPER_PAGE)] == [Quality.Q360]


def test_resolver_handles_only_blogger_video_pages():
    r = BloggerResolver()
    assert r.handles(StreamCandidate(host="blogger", url=IFRAME))
    assert r.handles(StreamCandidate(host="blogger", url=WRAPPER))
    assert not r.handles(StreamCandidate(host="x", url="https://x.com/embed/1"))
    assert not r.handles(StreamCandidate(host="x", url="https://blogger.com/blog/1"))


class _Http:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.calls = body, error, []

    async def get_text(self, url, *, params=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        if self.error:
            raise self.error
        return self.body


async def test_resolver_resolves_and_forwards_referer():
    http = _Http(BLOGGER_HTML)
    cand = StreamCandidate(host="blogger", url=IFRAME, headers={"Referer": BASE + "/"})
    streams = await BloggerResolver(http=http).resolve(cand)
    assert [s.quality for s in streams] == [Quality.Q720, Quality.Q360]
    assert http.calls == [(IFRAME, {"Referer": BASE + "/"})]
    # googlevideo 403s a non-browser UA, so the stream must carry one
    assert all("Firefox" in s.headers["User-Agent"] for s in streams)


async def test_resolver_errors_are_resolver_errors():
    cand = StreamCandidate(host="blogger", url=IFRAME)
    with pytest.raises(ResolverError):
        await BloggerResolver(http=_Http("<html>video removed</html>")).resolve(cand)
    with pytest.raises(ResolverError):
        await BloggerResolver(http=_Http(error=HttpError("boom"))).resolve(cand)


# -- provider over a fake HTTP ---------------------------------------------- #
class _FakeSite:
    def __init__(self, pages: dict[str, str]):
        self._pages, self.calls = pages, []

    async def get_text(self, url, *, params=None, headers=None):
        self.calls.append((url, params))
        if params and "s" in params:
            return self._pages["search"]
        for key, body in self._pages.items():
            if key != "search" and url.endswith(key):
                return body
        raise AssertionError(f"unexpected fetch: {url} {params}")


def _anime():
    return Anime(id=AnimeId(anilist=1), title=Title(romaji="Overgeared"))


async def test_dub_request_is_declined_without_fetching():
    http = _FakeSite({})
    prov = AnimepaheChProvider(http=http)
    assert await prov.find_sources(_anime(), Audio.DUB) == []
    assert http.calls == []


async def test_provider_pipeline_end_to_end():
    http = _FakeSite({
        "search": SEARCH_HTML,
        "/series/overgeared/": SERIES_HTML,
        "/overgeared-episode-1-english-subbed/": EPISODE_HTML,
    })
    prov = AnimepaheChProvider(http=http)
    assert (prov.name, prov.priority) == ("animepahe-ch", 50)

    sources = await prov.find_sources(_anime(), Audio.SUB)
    assert [s.anime_key for s in sources] == ["overgeared"]  # Frieren fuzzy-filtered out
    assert sources[0].provider == "animepahe-ch" and sources[0].confidence == 1.0
    assert http.calls[0] == (BASE + "/", {"s": "Overgeared"})

    ref = sources[0].ref()
    assert isinstance(ref, ProviderRef)
    eps = await prov.episodes(ref, AnimeId(anilist=1))
    assert [e.number for e in eps] == [1.0, 2.5, 3.0]

    cands = await prov.candidates(eps[0])
    assert len(cands) == 1
    c = cands[0]
    assert (c.host, c.url, c.audio) == ("blogger", IFRAME, Audio.SUB)
    assert c.headers == {"Referer": BASE + "/"}
    assert BloggerResolver().handles(c)  # the candidate is routed to the resolver


async def test_zorotv_pipeline_end_to_end():
    http = _FakeSite({
        "search": SEARCH_HTML.replace("/series/", "/anime/"),
        "/anime/overgeared/": SERIES_HTML,
        "/overgeared-episode-1-english-subbed/": WRAPPER_EPISODE_HTML,
    })
    prov = ZorotvProvider(http=http)
    assert (prov.name, prov.priority) == ("zorotv", 49)
    sources = await prov.find_sources(_anime(), Audio.SUB)
    assert [s.anime_key for s in sources] == ["overgeared"]
    assert http.calls[0] == ("https://zorotv.ba/", {"s": "Overgeared"})
    eps = await prov.episodes(sources[0].ref(), AnimeId(anilist=1))
    assert [e.number for e in eps] == [1.0, 2.5, 3.0]
    (c,) = await prov.candidates(eps[0])
    assert (c.host, c.url) == ("blogger", WRAPPER)
    assert c.headers == {"Referer": "https://zorotv.ba/"}
    assert BloggerResolver().handles(c)
    assert await prov.find_sources(_anime(), Audio.DUB) == []
