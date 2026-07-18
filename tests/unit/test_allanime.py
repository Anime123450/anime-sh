"""AllAnime provider: decoders (XOR + AES tobeparsed), fuzzy matching, and
candidate parsing — all offline via an injected fake HTTP client."""

from __future__ import annotations

import json

import pytest

from anime_sh.domain.models import Anime, AnimeId, Audio, Episode, ProviderRef, Status, Title
from anime_sh.infra.http import CloudflareChallenge
from anime_sh.providers.allanime.decode import (
    BUILD_ID,
    QUERY_HASH,
    build_aa_req,
    decode_source_url,
    decrypt_tobeparsed,
    encode_source_url,
    encrypt_tobeparsed,
)
from anime_sh.providers.allanime.provider import AllAnimeProvider, _best_match


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


def test_tobeparsed_roundtrip():
    payload = {"episode": {"sourceUrls": [{"sourceName": "Mp4", "sourceUrl": "x"}]}}
    blob = encrypt_tobeparsed(json.dumps(payload).encode())
    assert json.loads(decrypt_tobeparsed(blob).decode()) == payload


def test_aa_req_token_shape_and_stability():
    import base64

    # Fixed timestamp → deterministic token; decodes to \x01 + 12-byte nonce +
    # ciphertext + 16-byte GCM tag (AllAnime's aaReq layout).
    tok = build_aa_req(now=1_699_999_800)
    raw = base64.b64decode(tok)
    assert raw[0] == 1
    assert len(raw) >= 1 + 12 + 16
    # 5-minute rounding: instants in the same 300s bucket ([...800, ...100))
    # yield the same token; the next bucket differs.
    assert build_aa_req(now=1_699_999_800) == build_aa_req(now=1_700_000_099)
    assert build_aa_req(now=1_699_999_800) != build_aa_req(now=1_700_000_100)


def test_aa_req_verifies_under_the_shared_key():
    # The token must actually decrypt/authenticate with the AllAnime key, so a
    # key/epoch/build-id drift is caught here, not just live.
    import base64
    import hashlib
    import json as _json

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from anime_sh.providers.allanime.decode import _AES_KEY, _EPOCH

    raw = base64.b64decode(build_aa_req(now=1_700_000_100))
    nonce, ct_and_tag = raw[1:13], raw[13:]
    payload = _json.loads(AESGCM(_AES_KEY).decrypt(nonce, ct_and_tag, None))
    assert payload["qh"] == QUERY_HASH
    assert payload["epoch"] == _EPOCH and payload["buildId"] == str(BUILD_ID)
    ts = int((1_700_000_100 // 300) * 300 * 1000)
    assert payload["ts"] == ts
    # Nonce is sha256(iv_payload)[:12].
    iv_payload = f"{_EPOCH}:{BUILD_ID}:{QUERY_HASH}:{ts}"
    assert nonce == hashlib.sha256(iv_payload.encode()).digest()[:12]


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


def test_best_match_prefers_exact_title_while_airing():
    # AIRING show, planned 12 eps: the exact-title run with 2 aired must beat a
    # same-named side entry that already has all 12 (a different production).
    anime = Anime(
        id=AnimeId(anilist=196187),
        title=Title(romaji="Super no Ura de Yani Suu Futari",
                    english="Smoking Behind the Supermarket with You"),
        episode_count=12,
        status=Status.RELEASING,
    )
    edges = [
        {"_id": "mini", "name": "Super no Ura de Yani Suu Futari (Mini)",
         "availableEpisodes": {"sub": 12}},
        {"_id": "tv", "name": "Super no Ura de Yani Suu Futari",
         "availableEpisodes": {"sub": 2}},
    ]
    best = _best_match(anime, edges, "sub")
    assert best is not None and best["_id"] == "tv"


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
    provider = AllAnimeProvider(http=FakeHttp(get=payload))
    ref = ProviderRef(provider="allanime", anime_key="k", audio=Audio.SUB)
    ep = Episode(anime_id=AnimeId(anilist=1), number=1.0, provider_ref=ref, episode_key="1")
    cands = await provider.candidates(ep)

    # Higher priority first; internal clock decoded and pointed at allanime.day.
    assert cands[0].host == "Luf-Mp4"
    assert cands[0].url == "https://allanime.day/apivtwo/clock.json?id=abc"
    assert cands[1].host == "Mp4"


async def test_candidates_decrypts_tobeparsed():
    inner = {"episode": {"sourceUrls": [{"sourceName": "Mp4", "sourceUrl": "https://mp4upload.com/embed-y.html", "priority": 4}]}}
    blob = encrypt_tobeparsed(json.dumps(inner).encode())
    provider = AllAnimeProvider(http=FakeHttp(get={"data": {"tobeparsed": blob}}))
    ref = ProviderRef(provider="allanime", anime_key="k", audio=Audio.SUB)
    ep = Episode(anime_id=AnimeId(anilist=1), number=1.0, provider_ref=ref, episode_key="1")
    cands = await provider.candidates(ep)
    assert cands[0].host == "Mp4" and "mp4upload" in cands[0].url


async def test_cloudflare_challenge_surfaces_as_provider_unavailable():
    from anime_sh.domain.errors import ProviderUnavailable

    http = FakeHttp()
    http.raise_challenge = True
    provider = AllAnimeProvider(http=http)
    with pytest.raises(ProviderUnavailable):
        await provider.match(_anime(), Audio.SUB)
