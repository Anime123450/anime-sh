"""Downloads: ffmpeg command builder, DownloadService flow, and store round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from anime_sh.app.download import DownloadService, _safe
from anime_sh.app.playback import ResolvedPlayback
from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Audio,
    DownloadStatus,
    Episode,
    ProviderRef,
    Quality,
    Stream,
    StreamKind,
    Title,
)
from anime_sh.infra.db.database import Database
from anime_sh.infra.db.downloads import SqliteDownloadStore
from anime_sh.infra.db.library import SqliteLibrary
from anime_sh.infra.downloader.ffmpeg import build_ffmpeg_command


def _anime():
    return Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren"), episode_count=12)


def _stream(kind=StreamKind.HLS):
    return Stream(url="https://cdn/x.m3u8", kind=kind, quality=Quality.Q1080,
                  headers={"Referer": "https://host/"})


# -- command builder -------------------------------------------------------- #
def test_ffmpeg_command_hls():
    cmd = build_ffmpeg_command("ffmpeg", _stream(), Path("/out/ep.mp4"))
    assert cmd[0] == "ffmpeg"
    assert "-c" in cmd and "copy" in cmd
    assert "aac_adtstoasc" in cmd  # HLS remux fixup
    # Propagating protocol flags (reach variant playlists + segments).
    assert cmd[cmd.index("-referer") + 1] == "https://host/"
    assert "-user_agent" in cmd
    assert "-extension_picky" in cmd  # allow extensionless HLS segments
    assert cmd[-1].endswith("ep.mp4")


def test_ffmpeg_command_mp4_no_adts():
    cmd = build_ffmpeg_command("ffmpeg", _stream(StreamKind.MP4), Path("/out/ep.mp4"))
    assert "aac_adtstoasc" not in cmd
    assert "-extension_picky" not in cmd  # HLS-only


def test_safe_filename():
    assert _safe('Re:ZERO / Season 2?') == "ReZERO  Season 2"
    assert _safe("") == "anime"


# -- service flow ----------------------------------------------------------- #
class FakePlayback:
    def __init__(self, stream=None):
        self._stream = stream or _stream()
    async def resolve(self, anime, episode, *, audio=Audio.SUB):
        ref = ProviderRef(provider="p", anime_key="k", audio=audio)
        ep = Episode(anime_id=anime.id, number=episode, provider_ref=ref, episode_key="1")
        return ResolvedPlayback(ep, self._stream, 0)


class FakeDownloader:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
    def available(self):
        return True
    async def download(self, stream, dest, *, on_line=None):
        self.calls.append((stream.url, str(dest)))
        if self.fail:
            raise RuntimeError("ffmpeg boom")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake")


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.seq = 0
    async def add(self, anime_id, episode, path):
        self.seq += 1
        self.rows[self.seq] = {"status": DownloadStatus.QUEUED, "path": path}
        return self.seq
    async def set_status(self, download_id, status, *, path=None):
        self.rows[download_id]["status"] = status
        if path:
            self.rows[download_id]["path"] = path
    async def list(self, *, limit=50):
        return []


class FakeLibrary:
    def __init__(self):
        self.saved = []
    async def save_anime(self, anime):
        self.saved.append(anime)


def _service(tmp_path, downloader, store, library):
    return DownloadService(
        playback=FakePlayback(), downloader=downloader, store=store,
        library=library, download_dir=str(tmp_path),
    )


async def test_download_success_marks_done(tmp_path):
    dl, store, lib = FakeDownloader(), FakeStore(), FakeLibrary()
    svc = _service(tmp_path, dl, store, lib)
    dest = await svc.download(_anime(), 3.0)
    assert dest.exists()
    assert store.rows[1]["status"] is DownloadStatus.DONE
    assert lib.saved  # metadata cached for the downloads list
    assert dest.name == "Frieren - E3.mp4"


async def test_download_failure_marks_failed(tmp_path):
    dl, store, lib = FakeDownloader(fail=True), FakeStore(), FakeLibrary()
    svc = _service(tmp_path, dl, store, lib)
    with pytest.raises(RuntimeError):
        await svc.download(_anime(), 1.0)
    assert store.rows[1]["status"] is DownloadStatus.FAILED


# -- store round-trip ------------------------------------------------------- #
@pytest.fixture
async def db(tmp_path):
    d = Database(tmp_path / "anime.db", migrations_dir="migrations")
    await d.connect()
    yield d
    await d.close()


async def test_download_store_round_trip(db):
    lib = SqliteLibrary(db)
    await lib.save_anime(_anime())
    store = SqliteDownloadStore(db)
    did = await store.add(AnimeId(anilist=1), 3.0, "/tmp/x.mp4")
    await store.set_status(did, DownloadStatus.DONE, path="/tmp/x.mp4")
    items = await store.list()
    assert len(items) == 1
    assert items[0].status is DownloadStatus.DONE
    assert items[0].anime.title.preferred == "Frieren"  # joined from cache
    assert items[0].episode == 3.0
