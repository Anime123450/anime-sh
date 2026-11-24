"""Offline proofs for the two stream decryptors (fixtures captured live 2026-11-24).

* Flixcloud (reanime's host): page seed + per-request WASM mix + PBKDF2/AES-CBC
  hides the master URL; playlists then arrive base64(XOR(text, pk)).
* Megaplay (anikoto's/hianime's host): getSources answers ``{enc}``, AES-256-CBC
  with key/iv shipped in the player JS, plus an HMAC CDN token on the master.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from anime_sh.domain.models import Audio, StreamCandidate, StreamKind
from anime_sh.infra.proxy.deobfuscate import DeobfuscatingProxy, decode_xor_playlist
from anime_sh.resolvers.flixcloud import FlixcloudResolver
from anime_sh.resolvers.flixcloud import crypto as flix
from anime_sh.resolvers.vidtube import VidtubeResolver
from anime_sh.resolvers.vidtube import crypto as mega

FX = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- flixcloud
def _flix_fetch():
    exp = json.loads((FX / "flixcloud/expected.json").read_text())
    html = (FX / "flixcloud/embed_snippet.html").read_text(encoding="utf8")
    api = (FX / "flixcloud/api_m3u8.json").read_text(encoding="utf8")

    def fetch(url, headers):
        if "/e/" in url:
            return 200, html
        if url.endswith(exp["api_path"]):
            return 200, api
        return 404, ""

    return exp, fetch


def test_flixcloud_decrypts_master_url_offline():
    exp, fetch = _flix_fetch()
    r = flix.resolve(exp["embed_url"], fetch)
    assert r["url"].split("?")[0] == exp["url_prefix"]
    assert hashlib.sha256(r["url"].encode()).hexdigest() == exp["url_sha256"]
    assert r["playlist_key_b64"] == exp["playlist_key_b64"]
    assert r["subtitles"][0] == exp["first_subtitle"]


def test_flixcloud_field_names_follow_the_seed():
    a = flix.derive_fields("abc")
    assert a == flix.derive_fields("abc") and a != flix.derive_fields("abd")


def test_xor_playlist_roundtrip_and_passthrough():
    pk = bytes(range(1, 33))
    txt = "#EXTM3U\n#EXTINF:6,\nseg0.ts"
    enc = base64.b64encode(bytes(c ^ pk[i % 32] for i, c in enumerate(txt.encode()))).decode()
    assert decode_xor_playlist(enc, pk) == txt
    assert flix.decode_playlist(enc, pk) == txt
    assert decode_xor_playlist(txt, pk) == txt  # already clear: untouched


async def test_flixcloud_resolver_builds_proxied_hls_stream(monkeypatch):
    exp, fetch = _flix_fetch()
    res = FlixcloudResolver()
    monkeypatch.setattr(res, "_fetch", fetch)
    cand = StreamCandidate(host="flixcloud", url=exp["embed_url"], audio=Audio.SUB)
    assert res.handles(cand)
    [s] = await res.resolve(cand)
    assert s.kind is StreamKind.HLS and s.obfuscated
    assert s.playlist_key == exp["playlist_key_b64"]
    assert s.headers["Referer"].startswith("https://") and s.subtitles


# --------------------------------------------------------------------------- megaplay
MFX = json.loads((FX / "megaplay/getsources.json").read_text())
EXCERPT = (FX / "megaplay/e1_consts_excerpt.js").read_text()


def _mega_fetch(url, headers):
    if url.endswith("/lib/e1.js"):
        return 200, EXCERPT
    return 200, "<script src=\"https://x.test/lib/e1.js\"></script> type: 'sub'"


@pytest.mark.parametrize("case", MFX["cases"], ids=lambda c: c["name"])
def test_megaplay_decrypts_enc_and_tokenises(case):
    out = mega.decrypt_sources(case["getSources"], case["embed_url"], _mega_fetch)
    base = re.sub(r"&token=[^&]*$", "", out["file"])
    assert base == case["expected_file_without_token"]
    assert re.search(r"&token=[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$", out["file"])


def test_megaplay_token_format():
    t = mega.make_cdn_token("a" * 32 + "/" + "b" * 32, now=1000)
    msg, sig = t.split(".")
    assert mega._b64u_decode(msg) == b"1090|" + b"a" * 32 + b"/" + b"b" * 32
    assert len(mega._b64u_decode(sig)) == 32


def test_megaplay_rediscovers_rotated_key(monkeypatch):
    monkeypatch.setattr(mega, "SOURCE_ENC_KEY", "rotated-key-xxxx")
    c = MFX["cases"][0]
    out = mega.decrypt_sources(c["getSources"], c["embed_url"], _mega_fetch)
    assert out["file"].startswith(c["expected_file_without_token"])


class _FakeHttp:
    def __init__(self, data):
        self.data = data

    async def get_text(self, url, *, params=None, headers=None):
        return '<div data-id="13461"></div>'

    async def get_json(self, url, *, params=None, headers=None):
        return self.data


async def test_vidtube_resolver_opens_encrypted_sources(monkeypatch):
    c = MFX["cases"][0]
    res = VidtubeResolver(http=_FakeHttp(c["getSources"]))
    monkeypatch.setattr(res, "_fetch", _mega_fetch)
    [s] = await res.resolve(StreamCandidate(host="megaplay", url=c["embed_url"], audio=Audio.SUB))
    assert s.url.startswith(c["expected_file_without_token"]) and "token=" in s.url
    assert s.kind is StreamKind.HLS and s.obfuscated


# --------------------------------------------------------------------------- proxy
def test_proxy_rewrites_tag_uris_and_marks_children():
    p = DeobfuscatingProxy()
    p._base = "http://127.0.0.1:1"
    master = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="audio/index.m3u8"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo/index.m3u8\n"
    )
    out = p._rewrite_playlist(master, "https://cdn.test/x/master.m3u8?tok=1", "https://r/", "PK")
    lines = out.splitlines()
    assert 'URI="http://127.0.0.1:1/s?' in lines[1] and "&k=pl" in lines[1] and "&x=UEs=" in lines[1]
    assert lines[3].startswith("http://127.0.0.1:1/s?") and "&k=pl" in lines[3]
    variant = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x01\n#EXTINF:6,\nseg0.jpg\n'
    vl = p._rewrite_playlist(variant, "https://cdn.test/x/v/index.m3u8", "https://r/", "").splitlines()
    assert "&k=key" in vl[1] and "&k=" not in vl[3]
    u = base64.urlsafe_b64decode(re.search(r"u=([^&]+)", vl[3]).group(1)).decode()
    assert u == "https://cdn.test/x/v/seg0.jpg"
