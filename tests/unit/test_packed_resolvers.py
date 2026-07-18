"""Filemoon + Streamwish resolvers and the shared p.a.c.k.e.r unpacker."""

from __future__ import annotations

import pytest

from anime_sh.domain.errors import ResolverError
from anime_sh.domain.models import StreamCandidate, StreamKind
from anime_sh.resolvers.filemoon import FilemoonResolver
from anime_sh.resolvers.packed import extract_hls, unpack
from anime_sh.resolvers.streamwish import StreamwishResolver

# Classic Dean-Edwards packed sample that decodes to "hello world".
_PACKED_HELLO = (
    r"eval(function(p,a,c,k,e,d){e=function(c){return c};if(!''.replace(/^/,String))"
    r"{while(c--){d[c]=k[c]||c}k=[function(e){return d[e]}];e=function(){return'\w+'};"
    r"c=1};while(c--){if(k[c]){p=p.replace(new RegExp('\b'+e(c)+'\b','g'),k[c])}}return p}"
    r"('0 1',2,2,'hello|world'.split('|'),0,{}))"
)
# A packed player whose symtab reconstructs `file:"https://cdn.example/hls/master.m3u8"`.
_PACKED_PLAYER = r'''eval(function(p,a,c,k,e,d){}('3:"4://5.6/7/8.9"',12,12,'jwplayer|x|setup|file|https|cdn|example|hls|master|m3u8|type|hls'.split('|'),0,{}))'''


def test_unpack_decodes_classic_sample():
    assert unpack(_PACKED_HELLO) == "hello world"


def test_unpack_passthrough_when_not_packed():
    assert unpack("var file = 'x.m3u8'") == "var file = 'x.m3u8'"


def test_extract_hls_from_packed_and_plain():
    assert extract_hls(_PACKED_PLAYER) == "https://cdn.example/hls/master.m3u8"
    assert extract_hls('file:"https://c.dn/a/master.m3u8?t=1"') == "https://c.dn/a/master.m3u8?t=1"
    assert extract_hls("no stream here") is None


# -- resolver matching ------------------------------------------------------- #
def test_handles_matching_is_family_scoped():
    sw, fm = StreamwishResolver(), FilemoonResolver()
    assert sw.handles(StreamCandidate(host="Sw", url="https://streamwish.to/e/a"))
    assert fm.handles(StreamCandidate(host="Fm-Hls", url="https://bysekoze.com/e/a"))
    # Neither must claim the megaplay family (anikoto's Vidstream-*/HD-*).
    mega = StreamCandidate(host="Vidstream-2", url="https://megaplay.buzz/stream/s-2/1/sub")
    assert not sw.handles(mega) and not fm.handles(mega)


# -- resolve() over a fake HTTP ---------------------------------------------- #
class _FakePages:
    """Serves scripted page bodies keyed by URL substring; records requests."""

    def __init__(self, pages: dict[str, str]):
        self._pages = pages
        self.gets: list[str] = []

    async def get_text(self, url, *, params=None, headers=None):
        self.gets.append(url)
        for key, body in self._pages.items():
            if key in url:
                return body
        raise AssertionError(f"unexpected fetch: {url}")


async def test_streamwish_resolves_packed_m3u8():
    http = _FakePages({"streamwish.to": _PACKED_PLAYER})
    r = StreamwishResolver(http=http)
    streams = await r.resolve(StreamCandidate(host="Sw", url="https://streamwish.to/e/abc"))
    assert streams[0].url == "https://cdn.example/hls/master.m3u8"
    assert streams[0].kind is StreamKind.HLS


async def test_filemoon_follows_one_iframe_hop():
    outer = '<iframe src="https://filemoon.sx/e/inner123"></iframe>'
    http = _FakePages({"/e/outer": outer, "/e/inner123": _PACKED_PLAYER})
    r = FilemoonResolver(http=http)
    streams = await r.resolve(StreamCandidate(host="Fm-Hls", url="https://filemoon.sx/e/outer"))
    assert streams[0].url == "https://cdn.example/hls/master.m3u8"
    assert any("inner123" in g for g in http.gets)  # followed the iframe


async def test_resolver_errors_when_no_stream():
    http = _FakePages({"streamwish.to": "<html>no player</html>"})
    r = StreamwishResolver(http=http)
    with pytest.raises(ResolverError):
        await r.resolve(StreamCandidate(host="Sw", url="https://streamwish.to/e/x"))
