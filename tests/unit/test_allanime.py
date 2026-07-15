"""AllAnime provider: URL decoder, fuzzy matching, and candidate parsing —
all offline via an injected fake HTTP client."""

from __future__ import annotations

import json

import pytest

from anime_sh.domain.models import Anime, AnimeId, Audio, Episode, ProviderRef, Title
from anime_sh.infra.http import CloudflareChallenge
from anime_sh.providers.allanime.decode import decode_source_url, encode_source_url
from anime_sh.providers.allanime.provider import AllAnimeProvider, _best_match


# -- decoder ---------------------------------------------------------------- #
def test_decode_known_pairs():
    # Documented AllAnime hex->char mappings (XOR 0x38).
    assert decode_source_url("--01") == "9"
    assert decode_source_url("--08") == "0"
    assert decode_source_url("--00") == "8"


def test_decode_roundtrip():
    path = "/apivtwo/clock?id=AbC123-_xyz"
    assert decode_source_url(encode_source_url(path)) == path


def test_decode_passthrough_plain_url():
    assert decode_source_url("https://cdn/x.m3u8") == "https://cdn/x.m3u8"


def test_decode_malformed_is_returned_unchanged():
    assert decode_source_url("--zzz") == "--zzz"  # odd length / non-hex


# -- matching --------------------------------------------------------------- #
def _anime():
    return Anime(
        id=AnimeId(anilist=154587),
        title=Title(romaji="Sousou no Frieren", english="Frieren: Beyond Journey's End"),
        episode_count=28,
    )


def test_best_match_prefers_title_and_episode_count():
    edges = [
        {"_id": "x1", "name": "Sousou no Frieren", "englishName": None,
         "availableEpisodes": {"sub": 28}},
        {"_id": "x2", "name": "Some Other Show", "englishName": None,
         "availableEpisodes": {"sub": 12}},
    ]
    best = _best_match(_anime(), edges, "sub")
    assert best is not None and best["_id"] == "x1"
    assert best["_score"] >= 0.6


def test_best_match_rejects_weak_matches():
    edges = [{"_id": "z", "name": "Totally Unrelated Title",
              "englishName": None, "availableEpisodes": {"sub": 1}}]
    assert _best_match(_anime(), edges, "sub") is None


# -- provider methods over a fake HTTP -------------------------------------- #
class FakeHttp:
    def __init__(self, response):
        self._response = response
        self.raise_challenge = False

    async def get_json(self, url, *, params=None, headers=None):
        if self.raise_challenge:
            raise CloudflareChallenge("blocked")
        return self._response


async def test_candidates_decode_and_order_hosts():
    encoded = encode_source_url("/apivtwo/clock?id=abc")
    resp = {
        "data": {
            "episode": {
                "episodeString": "1",
                "sourceUrls": [
                    {"sourceName": "Yt-mp4", "sourceUrl": "https://cdn/low.mp4", "priority": 1},
                    {"sourceName": "Luf-mp4", "sourceUrl": encoded, "priority": 9},
                ],
            }
        }
    }
    provider = AllAnimeProvider(http=FakeHttp(resp))
    ref = ProviderRef(provider="allanime", anime_key="k", audio=Audio.SUB)
    ep = Episode(anime_id=AnimeId(anilist=1), number=1.0, provider_ref=ref, episode_key="1")
    cands = await provider.candidates(ep)

    # Luf-mp4 has higher host priority, so it sorts first; its clock URL is
    # decoded and pointed at the .json variant.
    assert cands[0].host == "Luf-mp4"
    assert "/apivtwo/clock.json?id=abc" in cands[0].url
    assert cands[1].host == "Yt-mp4"


async def test_cloudflare_challenge_surfaces_as_provider_unavailable():
    from anime_sh.domain.errors import ProviderUnavailable

    http = FakeHttp({})
    http.raise_challenge = True
    provider = AllAnimeProvider(http=http)
    with pytest.raises(ProviderUnavailable):
        await provider.match(_anime(), Audio.SUB)
