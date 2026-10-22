"""A downloads-folder change applies to the next job, never the running one."""

from __future__ import annotations

from pathlib import Path

from anime_sh.app.download import DownloadService
from anime_sh.app.playback import ResolvedPlayback
from anime_sh.domain.models import Stream, StreamKind

from .fakes import make_anime


class _Store:
    def __init__(self):
        self.rows = {}

    async def add(self, anime_id, episode, path):
        self.rows[len(self.rows) + 1] = {"path": path}
        return len(self.rows)

    async def set_status(self, download_id, status, *, path=None):
        self.rows[download_id]["status"] = status


class _Library:
    async def save_anime(self, anime):
        pass


class _Downloader:
    def available(self):
        return True

    async def download(self, stream, dest: Path, *, on_line=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"video")


class _Playback:
    """Resolving is the slow part of a job; the folder changes while it runs."""

    def __init__(self, on_resolve):
        self._on_resolve = on_resolve

    async def resolve(self, anime, episode, *, audio=None):
        self._on_resolve()
        return ResolvedPlayback(
            None, Stream(url="https://cdn/x.m3u8", kind=StreamKind.HLS), 0)


async def test_download_directory_change_applies_to_next_job(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    current = {"dir": str(old)}
    svc = DownloadService(
        playback=_Playback(lambda: current.update(dir=str(new))),
        downloader=_Downloader(), store=_Store(), library=_Library(),
        download_dir=lambda: current["dir"],
    )
    anime = make_anime()

    first = await svc.download(anime, 1.0)
    assert first.is_relative_to(old), "the running job moved to the new folder"
    assert first.is_file()

    second = await svc.download(anime, 2.0)
    assert second.is_relative_to(new)
    # Later "is it on disk?" checks look where downloads now go.
    assert svc.destination(anime, 1.0).is_relative_to(new)
    assert svc.local_path(anime, 2.0) == second
    assert svc.local_path(anime, 1.0) is None


async def test_unreadable_download_setting_falls_back_to_default(tmp_path):
    def broken():
        raise ValueError("bad config")

    svc = DownloadService(playback=_Playback(lambda: None), downloader=_Downloader(),
                          store=_Store(), library=_Library(), download_dir=broken)
    assert svc.destination(make_anime(), 1.0).parts[-3] == "anime"
    assert svc.local_path(make_anime(), 1.0) is None


def test_plain_path_still_works(tmp_path):
    svc = DownloadService(playback=_Playback(lambda: None), downloader=_Downloader(),
                          store=_Store(), library=_Library(), download_dir=tmp_path)
    assert svc.destination(make_anime(), 3.0).parent.parent == tmp_path
