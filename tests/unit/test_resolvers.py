"""Resolvers: quality mapping, generic passthrough, and AllAnime clock parsing."""

from __future__ import annotations

from anime_sh.domain.models import Quality, StreamCandidate, StreamKind
from anime_sh.resolvers.allanime.clock import AllAnimeClockResolver
from anime_sh.resolvers.generic import GenericStreamResolver
from anime_sh.resolvers.quality import kind_from_url, quality_from_str


def test_quality_from_str():
    assert quality_from_str("1080") == Quality.Q1080
    assert quality_from_str("1080p") == Quality.Q1080
    assert quality_from_str("4K") == Quality.Q2160
    assert quality_from_str(None) == Quality.UNKNOWN
    assert quality_from_str("weird") == Quality.UNKNOWN


def test_kind_from_url():
    assert kind_from_url("https://x/a.m3u8") == StreamKind.HLS
    assert kind_from_url("https://x/a.mp4?token=1") == StreamKind.MP4
    assert kind_from_url("https://x/a.mpd") == StreamKind.DASH


def test_generic_handles_and_resolves():
    r = GenericStreamResolver()
    assert r.handles(StreamCandidate(host="direct", url="https://x/a.m3u8"))
    assert not r.handles(StreamCandidate(host="x", url="https://x/embed/page"))


async def test_generic_passthrough():
    r = GenericStreamResolver()
    cand = StreamCandidate(host="direct", url="https://x/a.mp4", quality_hint="720")
    streams = await r.resolve(cand)
    assert streams[0].kind == StreamKind.MP4
    assert streams[0].quality == Quality.Q720


class _FakeHttp:
    def __init__(self, payload):
        self._payload = payload

    async def get_json(self, url, *, params=None, headers=None):
        return self._payload


def test_clock_handles():
    r = AllAnimeClockResolver(http=_FakeHttp({}))
    assert r.handles(StreamCandidate(host="Luf", url="https://api.allanime.day/apivtwo/clock.json?id=1"))
    assert not r.handles(StreamCandidate(host="x", url="https://x/a.m3u8"))


async def test_clock_resolves_links_to_streams():
    payload = {
        "links": [
            {"link": "https://cdn/1080.m3u8", "resolutionStr": "1080"},
            {"link": "https://cdn/720.mp4", "resolutionStr": "720"},
        ]
    }
    r = AllAnimeClockResolver(http=_FakeHttp(payload))
    cand = StreamCandidate(host="Luf", url="https://api.allanime.day/apivtwo/clock.json?id=1")
    streams = await r.resolve(cand)
    assert {s.quality for s in streams} == {Quality.Q1080, Quality.Q720}
    assert streams[0].kind == StreamKind.HLS


class _FakeHttpText:
    def __init__(self, text):
        self._text = text

    async def get_text(self, url, *, params=None, headers=None):
        return self._text


def test_mp4upload_handles():
    from anime_sh.resolvers.mp4upload import Mp4UploadResolver

    r = Mp4UploadResolver(http=_FakeHttpText(""))
    assert r.handles(StreamCandidate(host="Mp4", url="https://mp4upload.com/embed-x.html"))
    assert not r.handles(StreamCandidate(host="x", url="https://other/e/1"))


async def test_mp4upload_extracts_src():
    from anime_sh.resolvers.mp4upload import Mp4UploadResolver

    page = 'player.src({ type: "video/mp4", src: "https://a.mp4upload.com/d/x/video.mp4" });'
    r = Mp4UploadResolver(http=_FakeHttpText(page))
    streams = await r.resolve(StreamCandidate(host="Mp4", url="https://mp4upload.com/embed-x.html"))
    assert streams[0].url == "https://a.mp4upload.com/d/x/video.mp4"
    assert streams[0].kind == StreamKind.MP4


# -- megaplay family (vidtube.site / megaplay.buzz / vidwish.live) ----------- #
class _FakeMegaplayHttp:
    """Mimics the two-step embed-page -> getSources flow."""

    def __init__(self, cidu="6a5669db1ff22"):
        self._cidu = cidu
        self.getsources_id = None

    async def get_text(self, url, *, params=None, headers=None):
        return f"<script>const settings = {{ cidu : '{self._cidu}' }};</script>"

    async def get_json(self, url, *, params=None, headers=None):
        self.getsources_id = params["id"]
        return {
            "sources": {"file": "https://cdn.mewstream.buzz/anime/x/master.m3u8"},
            "tracks": [{"file": "https://sub/en.vtt", "label": "English", "kind": "captions", "default": True}],
            "intro": {"start": 0, "end": 0},
            "outro": {"start": 90, "end": 120},
        }


def test_megaplay_handles_family_hosts():
    from anime_sh.resolvers.vidtube import VidtubeResolver

    r = VidtubeResolver(http=_FakeMegaplayHttp())
    for host in ("vidtube.site", "megaplay.buzz", "vidwish.live"):
        assert r.handles(StreamCandidate(host="x", url=f"https://{host}/stream/s-5/1/sub"))
    assert not r.handles(StreamCandidate(host="x", url="https://other.tld/e/1"))


async def test_megaplay_resolves_via_cidu_and_getsources():
    from anime_sh.resolvers.vidtube import VidtubeResolver

    http = _FakeMegaplayHttp(cidu="ABC123")
    r = VidtubeResolver(http=http)
    cand = StreamCandidate(host="HD-1", url="https://megaplay.buzz/stream/s-5/830671/sub")
    streams = await r.resolve(cand)
    # File id comes from the page cidu, NOT the URL's 830671.
    assert http.getsources_id == "ABC123"
    s = streams[0]
    assert s.url.endswith("master.m3u8") and s.kind == StreamKind.HLS
    assert len(s.subtitles) == 1 and s.subtitles[0].label == "English"
    # intro 0..0 ignored; outro 90..120 becomes a skip range.
    assert s.skip_times is not None and s.skip_times.ed.start_s == 90
