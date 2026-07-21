"""AllAnime provider: decoders (XOR + GCM tobeparsed + aaReq), fuzzy matching,
and candidate parsing — all offline via an injected fake HTTP client."""

from __future__ import annotations

import base64
import json

import pytest

from anime_sh.domain.models import Anime, AnimeId, Audio, Episode, ProviderRef, Title
from anime_sh.infra.http import CloudflareChallenge
from anime_sh.providers.allanime.decode import (
    build_aareq,
    decode_source_url,
    decrypt_tobeparsed,
    encode_source_url,
    encrypt_tobeparsed,
)
from anime_sh.providers.allanime.keygen import (
    Keygen,
    derive_key,
    parse_aacrypto,
    resolve_source_query,
)
from anime_sh.providers.allanime.provider import AllAnimeProvider, _best_match

# A deterministic 32-byte key for signing/decryption in offline tests.
_TEST_KEY_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_TEST_KEYGEN = Keygen(
    epoch=6884, key=_TEST_KEY_HEX, query_hash="deadbeef", query="query { episode }"
)


# -- decoders --------------------------------------------------------------- #
def test_decode_known_pairs():
    assert decode_source_url("--01") == "9"
    assert decode_source_url("--08") == "0"
    assert decode_source_url("--00") == "8"


def test_decode_roundtrip():
    path = "/apivtwo/clock?id=AbC123-_xyz"
    assert decode_source_url(encode_source_url(path)) == path


def test_decode_passthrough_plain_url():
    assert decode_source_url("https://cdn/x.m3u8") == "https://cdn/x.m3u8"


def test_tobeparsed_roundtrip_legacy_key():
    payload = {"episode": {"sourceUrls": [{"sourceName": "Mp4", "sourceUrl": "x"}]}}
    blob = encrypt_tobeparsed(json.dumps(payload).encode())
    # No key given -> the legacy static key both seals and (as fallback) opens it.
    assert json.loads(decrypt_tobeparsed(blob).decode()) == payload


def test_tobeparsed_roundtrip_rotating_key():
    key = bytes.fromhex(_TEST_KEY_HEX)
    payload = {"episode": {"sourceUrls": []}}
    blob = encrypt_tobeparsed(json.dumps(payload).encode(), key)
    assert json.loads(decrypt_tobeparsed(blob, key).decode()) == payload


def test_build_aareq_shape():
    key = bytes.fromhex(_TEST_KEY_HEX)
    token = build_aareq(key, "deadbeef", 6884, now_ms=1_784_616_000_000)
    raw = base64.b64decode(token)
    # version byte, 12-byte IV, then ciphertext+16-byte GCM tag.
    assert raw[0] == 1
    assert len(raw) > 1 + 12 + 16


# -- matching --------------------------------------------------------------- #
def _anime():
    return Anime(
        id=AnimeId(anilist=154587),
        title=Title(romaji="Sousou no Frieren", english="Frieren: Beyond Journey's End"),
        episode_count=28,
    )


def test_best_match_prefers_title_and_episode_count():
    edges = [
        {"_id": "x1", "name": "Sousou no Frieren", "availableEpisodes": {"sub": 28}},
        {"_id": "x2", "name": "Some Other Show", "availableEpisodes": {"sub": 12}},
    ]
    best = _best_match(_anime(), edges, "sub")
    assert best is not None and best["_id"] == "x1"


def test_best_match_rejects_weak_matches():
    edges = [{"_id": "z", "name": "Totally Unrelated", "availableEpisodes": {"sub": 1}}]
    assert _best_match(_anime(), edges, "sub") is None


# -- provider over a fake HTTP ---------------------------------------------- #
class FakeHttp:
    def __init__(self, *, get=None, post=None):
        self._get = get
        self._post = post
        self.raise_challenge = False

    async def get_json(self, url, *, params=None, headers=None):
        if self.raise_challenge:
            raise CloudflareChallenge("blocked")
        return self._get

    async def post_json(self, url, *, json=None, headers=None):
        if self.raise_challenge:
            raise CloudflareChallenge("blocked")
        return self._post


async def test_candidates_decode_and_order_by_priority():
    encoded = encode_source_url("/apivtwo/clock?id=abc")
    payload = {
        "data": {
            "episode": {
                "sourceUrls": [
                    {"sourceName": "Mp4", "sourceUrl": "https://mp4upload.com/embed-x.html", "priority": 4},
                    {"sourceName": "Luf-Mp4", "sourceUrl": encoded, "priority": 7.5},
                ]
            }
        }
    }
    provider = AllAnimeProvider(http=FakeHttp(get=payload), keygen=_TEST_KEYGEN)
    ref = ProviderRef(provider="allanime", anime_key="k", audio=Audio.SUB)
    ep = Episode(anime_id=AnimeId(anilist=1), number=1.0, provider_ref=ref, episode_key="1")
    cands = await provider.candidates(ep)

    # Higher priority first; internal clock decoded and pointed at allanime.day.
    assert cands[0].host == "Luf-Mp4"
    assert cands[0].url == "https://allanime.day/apivtwo/clock.json?id=abc"
    assert cands[1].host == "Mp4"


async def test_candidates_decrypts_tobeparsed():
    inner = {"episode": {"sourceUrls": [{"sourceName": "Mp4", "sourceUrl": "https://mp4upload.com/embed-y.html", "priority": 4}]}}
    # Sealed with the rotating per-build key from the injected keygen.
    blob = encrypt_tobeparsed(json.dumps(inner).encode(), _TEST_KEYGEN.key_bytes)
    provider = AllAnimeProvider(http=FakeHttp(get={"data": {"tobeparsed": blob}}), keygen=_TEST_KEYGEN)
    ref = ProviderRef(provider="allanime", anime_key="k", audio=Audio.SUB)
    ep = Episode(anime_id=AnimeId(anilist=1), number=1.0, provider_ref=ref, episode_key="1")
    cands = await provider.candidates(ep)
    assert cands[0].host == "Mp4" and "mp4upload" in cands[0].url


async def test_candidates_empty_on_api_error():
    # The API declined (uncached persisted query / crypto rotation): no crash.
    payload = {"errors": [{"message": "PersistedQueryNotFound"}], "data": {"episode": None}}
    provider = AllAnimeProvider(http=FakeHttp(get=payload), keygen=_TEST_KEYGEN)
    ref = ProviderRef(provider="allanime", anime_key="k", audio=Audio.SUB)
    ep = Episode(anime_id=AnimeId(anilist=1), number=1.0, provider_ref=ref, episode_key="1")
    assert await provider.candidates(ep) == []


def test_keygen_parse_aacrypto():
    part_b = bytes(range(32))
    import base64 as _b64

    html = (
        'foo <script>window.__aaCrypto={"epoch":6884,"partB":"'
        + _b64.b64encode(part_b).decode()
        + '"};</script> bar'
    )
    epoch, pb = parse_aacrypto(html)
    assert epoch == 6884 and pb == part_b


def test_keygen_derive_key_is_mask_xor_partb():
    mask_hex = "ff" * 32
    part_b = bytes([0x0F]) * 32
    assert derive_key(mask_hex, part_b) == bytes([0xF0]) * 32


def test_keygen_resolve_source_query_interpolates_template():
    # A minimal crypto chunk: a source query template with a ${extra} fragment.
    chunk = (
        "const extra=`thumbnail\nnotes`;\n"
        "const q=`\nquery( $showId: String! ) {\nepisode( showId: $showId ) "
        "{\nepisodeString\nsourceUrls\n${extra}\n}\n}\n`;"
    )
    query = resolve_source_query(chunk)
    assert query is not None
    assert "sourceUrls" in query and "thumbnail" in query and "${" not in query


async def test_cloudflare_challenge_surfaces_as_provider_unavailable():
    from anime_sh.domain.errors import ProviderUnavailable

    http = FakeHttp()
    http.raise_challenge = True
    provider = AllAnimeProvider(http=http)
    with pytest.raises(ProviderUnavailable):
        await provider.match(_anime(), Audio.SUB)
