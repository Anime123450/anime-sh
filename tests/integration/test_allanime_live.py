"""Live AllAnime provider check — proves the current protocol works end to end:
Cloudflare-passing headers on the mkissa.to origin, POST search, aaReq-signed
persisted-query sources, AES-256-GCM tobeparsed decrypt, and XOR source-URL
decode.

Gated behind ANIME_SH_LIVE=1 because it hits the real AllAnime API. Resolving a
candidate to a playable file is deliberately NOT asserted here — individual host
availability is flaky by nature; that's what the resolver fallback chain is for.
"""

from __future__ import annotations

import os

import pytest

from anime_sh.domain.models import Audio
from anime_sh.infra.metadata import AniListMetadata
from anime_sh.providers.allanime import AllAnimeProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("ANIME_SH_LIVE") != "1",
    reason="live AllAnime test; set ANIME_SH_LIVE=1",
)


async def test_allanime_match_episodes_and_candidates():
    md = AniListMetadata()
    provider = AllAnimeProvider()
    try:
        anime = (await md.search("Frieren", limit=1))[0]
        ref = await provider.match(anime, Audio.SUB)
        assert ref is not None, "AllAnime should match Frieren"

        episodes = await provider.episodes(ref, anime.id)
        assert len(episodes) >= 12, "Frieren should list many episodes"

        candidates = await provider.candidates(episodes[0])
        # The provider decrypted + decoded real hosts, even if none resolve now.
        assert candidates, "expected decoded stream candidates"
        assert all(c.url.startswith("http") for c in candidates)
    finally:
        await md.aclose()
