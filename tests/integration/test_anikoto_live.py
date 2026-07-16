"""Live anikoto end-to-end — the money path for a show AllAnime lacks the full
run of: AniList identity -> anikoto match/episodes/candidates -> megaplay
resolver -> a real .m3u8.

Gated behind ANIME_SH_LIVE=1 (hits anikototv.to + its megaplay hosts).
"""

from __future__ import annotations

import os

import pytest

from anime_sh.domain.models import Audio, StreamKind
from anime_sh.infra.metadata import AniListMetadata
from anime_sh.providers.anikoto import AnikotoProvider
from anime_sh.resolvers.vidtube import VidtubeResolver

pytestmark = pytest.mark.skipif(
    os.environ.get("ANIME_SH_LIVE") != "1",
    reason="live anikoto test; set ANIME_SH_LIVE=1",
)


async def test_anikoto_resolves_show_allanime_lacks():
    md = AniListMetadata()
    provider = AnikotoProvider()
    resolver = VidtubeResolver()
    try:
        anime = (await md.search("Smoking Behind the Supermarket with You", limit=1))[0]
        ref = await provider.match(anime, Audio.SUB)
        assert ref is not None, "anikoto should match the show"

        episodes = await provider.episodes(ref, anime.id)
        assert episodes, "expected at least one episode"

        candidates = await provider.candidates(episodes[0])
        assert candidates, "expected server candidates"

        # At least one megaplay-family host should resolve to a real playlist.
        resolved = None
        for cand in candidates:
            if not resolver.handles(cand):
                continue
            try:
                streams = await resolver.resolve(cand)
            except Exception:
                continue
            if streams:
                resolved = streams[0]
                break
        assert resolved is not None, "no megaplay host resolved"
        assert resolved.kind == StreamKind.HLS
        assert ".m3u8" in resolved.url
    finally:
        await md.aclose()
