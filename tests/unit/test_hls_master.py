"""Splitting a multi-dub HLS master into "one variant + one audio track".

Regression for Overgeared (2026-11-19): ZokoAnime's master listed 15 audio
renditions and 4 video variants. mpv probed all of them before the first frame
(31 s), playback's 25 s start window expired, and every episode was reported as
unplayable. Opening one variant plus one audio track started in ~8 s.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from anime_sh.domain.models import Audio, Quality, Stream, StreamCandidate, StreamKind
from anime_sh.infra.downloader.ffmpeg import build_ffmpeg_command
from anime_sh.infra.players.mpv import MpvPlayer
from anime_sh.infra.players.vlc import VlcPlayer
from anime_sh.resolvers.hls_master import split_master
from anime_sh.resolvers.zoko.resolver import ZokoResolver

BASE = "https://hls.example.top/p/a/b/master.m3u8"
MASTER = """#EXTM3U
#EXT-X-VERSION:4
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Japanese",LANGUAGE="ja",DEFAULT=YES,AUTOSELECT=YES,URI="aud/a0/index.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",DEFAULT=NO,AUTOSELECT=YES,URI="aud/a1/index.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Hindi",LANGUAGE="hi",DEFAULT=NO,AUTOSELECT=YES,URI="aud/a5/index.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,AUDIO="aud"
360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,AUDIO="aud"
1080/index.m3u8
"""


def test_each_variant_gets_the_default_audio_track():
    variants = split_master(MASTER, BASE)
    assert [(v.quality, v.url) for v in variants] == [
        (Quality.Q360, "https://hls.example.top/p/a/b/360/index.m3u8"),
        (Quality.Q1080, "https://hls.example.top/p/a/b/1080/index.m3u8"),
    ]
    assert {v.audio_url for v in variants} == {
        "https://hls.example.top/p/a/b/aud/a0/index.m3u8"
    }


def test_preferred_language_wins_over_default():
    (v, _) = split_master(MASTER, BASE, prefer_lang="en")
    assert v.audio_url.endswith("aud/a1/index.m3u8")


def test_muxed_audio_or_a_media_playlist_is_left_alone():
    muxed = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1280x720\n720.m3u8\n"
    assert split_master(muxed, BASE) == []
    assert split_master("#EXTM3U\n#EXTINF:4,\nseg0.ts\n", BASE) == []
    assert split_master("<html>not a playlist</html>", BASE) == []


# -- zoko end to end ---------------------------------------------------------- #
class _Http:
    def __init__(self, page: str, master: str | Exception):
        self.page, self.master = page, master

    async def get_text(self, url, *, params=None, headers=None):
        if url.endswith(".m3u8"):
            if isinstance(self.master, Exception):
                raise self.master
            return self.master
        return self.page

    async def aclose(self):
        pass


def _page(src: str) -> str:
    plain = json.dumps({"download_url": "/d", "src": src}).encode()
    key = b"otaku-embed-v1"
    payload = base64.b64encode(bytes(b ^ key[i % len(key)] for i, b in enumerate(plain)))
    return f'<script>window.__P="{payload.decode()}"</script>'


def _cand(audio="sub"):
    return StreamCandidate(
        host="ZokoAnime", url=f"https://zokoanime.video/stream/mal/1/1/{audio}",
        audio=Audio.SUB,
    )


async def test_zoko_returns_split_variants_with_the_referer():
    streams = await ZokoResolver(http=_Http(_page(BASE), MASTER)).resolve(_cand())
    assert [s.quality for s in streams] == [Quality.Q360, Quality.Q1080]
    assert all(s.audio_url.endswith("aud/a0/index.m3u8") for s in streams)
    assert all(s.headers["Referer"] == "https://zokoanime.video/" for s in streams)


async def test_zoko_dub_picks_the_english_track():
    streams = await ZokoResolver(http=_Http(_page(BASE), MASTER)).resolve(_cand("dub"))
    assert streams[0].audio_url.endswith("aud/a1/index.m3u8")


async def test_zoko_falls_back_to_the_master_when_the_split_fails():
    streams = await ZokoResolver(http=_Http(_page(BASE), OSError("boom"))).resolve(_cand())
    assert [(s.url, s.audio_url) for s in streams] == [(BASE, None)]


# -- consumers ---------------------------------------------------------------- #
SPLIT = Stream(
    url="https://cdn.example/1080/index.m3u8", kind=StreamKind.HLS,
    headers={"Referer": "https://zokoanime.video/"},
    audio_url="https://cdn.example/aud/a0/index.m3u8",
)


def test_mpv_gets_the_audio_track_as_an_audio_file():
    args = MpvPlayer()._build_args("mpv", SPLIT, "t", 0, "/tmp/s")
    assert f"--audio-file={SPLIT.audio_url}" in args
    assert args[-1] == SPLIT.url


def test_vlc_gets_the_audio_track_as_an_input_slave():
    vlc = VlcPlayer("vlc", spawn=None, client_factory=None, pick_port=lambda: 1)
    argv = vlc._argv(SPLIT, "t", 0, 1, "pw")
    assert f"--input-slave={SPLIT.audio_url}" in argv


def test_ffmpeg_downloads_both_inputs_with_the_referer_on_each():
    cmd = build_ffmpeg_command("ffmpeg", SPLIT, Path("out.mp4"))
    inputs = [i for i, a in enumerate(cmd) if a == "-i"]
    assert [cmd[i + 1] for i in inputs] == [SPLIT.url, SPLIT.audio_url]
    assert cmd.count("-referer") == 2
    assert cmd[cmd.index("-map") : cmd.index("-map") + 4] == ["-map", "0:v:0", "-map", "1:a:0"]
