"""Live hianime end-to-end: AniList identity → hianime match / episodes /
candidates → the zoko resolver → a real ``.m3u8`` that actually serves.

Gated behind ANIME_SH_LIVE=1 (hits hianime.at, zokoanime.video and the CDN).

This one fetches the playlist rather than stopping at the URL. The resolver
returning a plausible-looking link is exactly the thing that can be true while
nothing plays, and the whole point of the provider is a stream that opens.
"""

from __future__ import annotations

import os

import pytest

from anime_sh.domain.errors import MetadataError
from anime_sh.domain.models import AnimeId, Audio, StreamKind
from anime_sh.infra.http import HttpClient
from anime_sh.infra.metadata import AniListMetadata
from anime_sh.providers.hianime import HianimeProvider
from anime_sh.resolvers.zoko import ZokoResolver

pytestmark = pytest.mark.skipif(
    os.environ.get("ANIME_SH_LIVE") != "1",
    reason="live hianime test; set ANIME_SH_LIVE=1",
)

# Frieren: Beyond Journey's End — 28 episodes, finished, sub and dub everywhere.
FRIEREN_ANILIST_ID = 154587


@pytest.mark.parametrize("audio", [Audio.SUB, Audio.DUB])
async def test_hianime_resolves_a_playable_stream(audio):
    md = AniListMetadata()
    provider = HianimeProvider()
    resolver = ZokoResolver()
    http = HttpClient(headers={"Referer": "https://zokoanime.video/"})
    try:
        # The identity lookup is a precondition of this probe, not part of it.
        # AniList disabled its own public API on 07/09/2026; failing here would
        # report "hianime is broken" about a provider never contacted — the exact
        # false alarm the canary's `blocked` status was added for.
        #
        # Fetched by id, not by title. This test searched for "Frieren: Beyond
        # Journey's End" until 17/09/2026, when AniList's own ranking put a
        # spin-off first and the test followed it onto an entry with no dub —
        # reporting a hianime regression that did not exist. Which show we mean
        # is not the thing under test, so it is stated rather than searched for.
        try:
            anime = await md.get(AnimeId(anilist=FRIEREN_ANILIST_ID))
        except MetadataError as e:
            pytest.skip(f"AniList unavailable, provider not reached: {e}")
        assert anime.id.anilist == FRIEREN_ANILIST_ID
        ref = await provider.match(anime, audio)
        assert ref is not None, "hianime should match the show"

        episodes = await provider.episodes(ref, anime.id)
        assert episodes, "expected at least one episode"
        assert episodes[0].number == 1.0

        candidates = await provider.candidates(episodes[0])
        assert candidates, "expected server candidates"

        stream = None
        for cand in candidates:
            if not resolver.handles(cand):
                continue
            streams = await resolver.resolve(cand)
            if streams:
                stream = streams[0]
                break
        assert stream is not None, "no zoko host resolved"
        assert stream.kind is StreamKind.HLS
        assert ".m3u8" in stream.url

        # A URL is not a stream. The master playlist has to actually come back.
        playlist = await http.get_text(stream.url)
        assert playlist.lstrip().startswith("#EXTM3U")
        assert "#EXT-X-STREAM-INF" in playlist, "expected variant streams"
    finally:
        await md.aclose()
        await provider.aclose()
        await resolver.aclose()
        await http.aclose()
