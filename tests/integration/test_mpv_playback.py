"""Live end-to-end money path with the REAL mpv player and REAL SQLite library.

Substitutes a controllable public HLS stream for a provider (which is just one
swappable adapter), so we can prove the rest of the chain works for real:

    PlaybackService.play_and_track
        -> ProviderManager -> fake provider yields the test stream candidate
        -> GenericStreamResolver -> Stream
        -> MpvPlayer launches mpv over IPC (headless, --length caps runtime)
        -> events() drives throttled progress writes
        -> SqliteLibrary persists WatchProgress

Gated behind ANIME_SH_LIVE=1 and mpv on PATH, so CI's offline suite skips it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from anime_sh.app.playback import PlaybackService
from anime_sh.app.providers import ProviderManager
from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Audio,
    Episode,
    ProviderRef,
    StreamCandidate,
    Title,
)
from anime_sh.infra.db.database import Database
from anime_sh.infra.db.library import SqliteLibrary
from anime_sh.infra.players import MpvPlayer
from anime_sh.resolvers.generic import GenericStreamResolver

pytestmark = pytest.mark.skipif(
    os.environ.get("ANIME_SH_LIVE") != "1" or shutil.which("mpv") is None,
    reason="live mpv test; set ANIME_SH_LIVE=1 with mpv installed",
)

TEST_STREAM = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"


class _StreamProvider:
    """A stand-in provider that yields the public test stream as a candidate."""

    name = "test"
    priority = 100
    api_version = 1

    async def match(self, anime, audio):
        return ProviderRef(provider=self.name, anime_key="test-key", audio=audio)

    async def episodes(self, ref, anime_id):
        return [
            Episode(anime_id=anime_id, number=1.0, provider_ref=ref, episode_key="1")
        ]

    async def candidates(self, episode):
        return [StreamCandidate(host="direct", url=TEST_STREAM)]


async def test_real_mpv_playback_saves_progress(tmp_path: Path):
    db = Database(tmp_path / "anime.db", migrations_dir="migrations")
    await db.connect()
    library = SqliteLibrary(db)

    svc = PlaybackService(
        providers=ProviderManager([_StreamProvider()]),
        resolvers=[GenericStreamResolver()],
        # Headless, and cap playback so the test finishes quickly.
        player=MpvPlayer(extra_args=["--vo=null", "--ao=null", "--length=4"]),
        library=library,
        quality="best",
    )
    anime = Anime(id=AnimeId(anilist=154587), title=Title(romaji="Frieren"))

    await svc.play_and_track(anime, 1.0, audio=Audio.SUB)

    progress = await library.get_progress(anime.id, 1.0)
    await db.close()

    assert progress is not None, "expected progress to be persisted"
    assert progress.position_s >= 0
    assert progress.duration_s > 0, "mpv should have reported a duration over IPC"
