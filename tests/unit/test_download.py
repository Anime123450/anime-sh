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


async def test_cancelling_a_download_kills_ffmpeg(monkeypatch, tmp_path):
    """Abandoning a download must not leave ffmpeg running.

    The stderr pump had no cleanup, so cancelling the worker (quit, Ctrl-C) left
    the child process alive and still writing to the destination file.
    """
    import asyncio as aio

    from anime_sh.infra.downloader.ffmpeg import FfmpegDownloader

    class FakeProc:
        def __init__(self):
            self.returncode = None
            self.killed = False
            self.stderr = self

        def __aiter__(self):
            return self

        async def __anext__(self):
            await aio.sleep(3600)  # hang like a long download

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    proc = FakeProc()

    async def fake_exec(*a, **k):
        return proc

    monkeypatch.setattr("shutil.which", lambda _: "ffmpeg")
    monkeypatch.setattr(aio, "create_subprocess_exec", fake_exec)

    task = aio.ensure_future(
        FfmpegDownloader().download(_stream(), tmp_path / "ep.mp4")
    )
    await aio.sleep(0)  # let it reach the stderr pump
    task.cancel()
    with pytest.raises(aio.CancelledError):
        await task
    assert proc.killed, "ffmpeg was left running after cancellation"


def test_windows_exit_codes_are_shown_signed():
    """Windows reports a negative status as 32-bit unsigned, so an ordinary
    ffmpeg failure read as "ffmpeg exited 4294967291"."""
    from anime_sh.infra.downloader.ffmpeg import _signed

    assert _signed(4294967291) == -5
    assert _signed(1) == 1
    assert _signed(0) == 0
    assert _signed(None) is None


def test_download_names_are_safe_on_every_platform():
    """Titles come from AniList and end up as folder and file names twice over.

    Path separators were already stripped (no traversal), but three gaps
    remained: Windows refuses reserved device names, a leading dash makes the
    path look like a flag to whatever tool receives it, and long light-novel
    titles push the full path toward Windows' 260-character limit — a
    96-character title already reaches 229 with the *default* download folder.
    """
    from anime_sh.app.download import _MAX_COMPONENT, _safe

    # No traversal: separators are removed, not resolved.
    assert "/" not in _safe("../../etc/passwd")
    assert "\\" not in _safe("..\..\windows\system32")
    assert _safe("..") == "anime"

    # Reserved device names are escaped rather than left to fail at write time.
    for reserved in ("CON", "NUL", "com1", "LPT1.mp4", "aux"):
        assert _safe(reserved).startswith("_"), reserved

    # A leading dash must not survive into an argv position.
    assert not _safe("-i").startswith("-")
    assert not _safe("-rf --output=/tmp/x").startswith("-")

    # Long titles are capped, and never end in a dot or space.
    long_title = "A" * 300
    assert len(_safe(long_title)) <= _MAX_COMPONENT
    real = ("Rich Girl Caretaker: I'm Secretly the Caregiver of the Most "
            "Popular Girl in This Rich Kid School")
    capped = _safe(real)
    assert len(capped) <= _MAX_COMPONENT
    assert capped == capped.rstrip(". ")

    # Ordinary titles are left alone — including a real one that starts with a dot.
    assert _safe("Frieren: Beyond Journey's End") == "Frieren Beyond Journey's End"
    assert _safe(".hack//SIGN") == ".hackSIGN"


def test_long_titles_that_share_a_prefix_get_different_names():
    """Truncating to a length cap threw away what distinguishes two seasons.

    The season marker sits at the *end* of a long title, so plain truncation
    gave "…Rich Kid School" and "…Rich Kid School Season 2" the identical folder
    AND file name — downloading one would silently overwrite the other's
    episodes. A path-length failure is loud; this was not.
    """
    from anime_sh.app.download import _MAX_COMPONENT, _safe

    base = ("Rich Girl Caretaker: I'm Secretly the Caregiver of the Most "
            "Popular Girl in This Rich Kid School")
    names = [_safe(base), _safe(base + " Season 2"), _safe(base + " Season 3")]
    assert len(set(names)) == 3, f"distinct titles collided: {names}"
    assert all(len(n) <= _MAX_COMPONENT for n in names)
    # Deterministic, so a re-download resolves to the file already on disk.
    assert _safe(base) == _safe(base)
    # Titles short enough to keep whole are untouched by the uniquifier.
    assert _safe("Frieren: Beyond Journey's End") == "Frieren Beyond Journey's End"
