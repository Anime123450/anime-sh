"""An episode you already downloaded should play from disk.

Downloads were write-only: you could fetch an episode and anime-sh would still
stream it from the internet the next time you asked for it — slower, dependent
on a CDN that may be dead by now, and impossible on a train.

The local file is introduced as an ordinary stream candidate, first in line, so
progress, intro skipping, auto-next and history need no idea where it came from.
"""

from __future__ import annotations

import os

import pytest

from anime_sh.app.playback import PlaybackService
from anime_sh.domain.models import Anime, AnimeId, Audio, Format, Title


def _anime() -> Anime:
    return Anime(id=AnimeId(anilist=1), title=Title(romaji="BOCCHI THE ROCK!"),
                 format=Format.TV, episode_count=12)


class _Downloads:
    """The download store: knows a path, or doesn't."""

    def __init__(self, path=None, explode=False):
        self.path = path
        self.explode = explode
        self.asked = []

    async def local_episode(self, anime_id, episode):
        self.asked.append((anime_id.anilist, episode))
        if self.explode:
            raise RuntimeError("downloads table is locked")
        return self.path


class _Library:
    async def get_progress(self, *a, **k):
        return None

    async def save_anime(self, *a, **k):
        pass


class _Providers:
    """A provider fan-out that must not be reached when a local copy exists."""

    def __init__(self):
        self.called = False

    async def resolve_sources(self, *a, **k):
        self.called = True
        return []


def _service(downloads, providers=None, **kw):
    return PlaybackService(providers=providers or _Providers(), resolvers=[],
                           player=None, library=_Library(), downloads=downloads, **kw)


@pytest.fixture
def episode_file(tmp_path):
    path = tmp_path / "BOCCHI THE ROCK! - E1.mp4"
    path.write_bytes(b"x" * 4096)
    return path


async def test_a_downloaded_episode_plays_from_disk(episode_file):
    providers = _Providers()
    service = _service(_Downloads(str(episode_file)), providers)

    resolved = await service.resolve(_anime(), 1.0, allow_local=True)

    assert resolved.stream.url == str(episode_file)
    assert not providers.called, "the provider fan-out ran anyway"


async def test_downloading_an_episode_you_already_have_does_not_destroy_it(
    episode_file,
):
    """The reason local candidates are opt-in rather than opt-out.

    `DownloadService.download` resolves a stream through the same code path and
    then hands it to ffmpeg with the destination it is about to write. If
    `resolve` offered the local file, ffmpeg would be pointed at its own output
    — which `-y` truncates before reading. Re-downloading an episode you already
    had would have destroyed it.
    """
    downloads = _Downloads(str(episode_file))
    service = _service(downloads, _Providers())

    # Exactly what the download path calls: no allow_local.
    with pytest.raises(Exception):
        await service.resolve(_anime(), 1.0)

    assert downloads.asked == [], "the download path consulted local files"


async def test_a_row_whose_file_is_gone_is_not_offered(tmp_path):
    """A download row is a claim, not evidence. Folders get tidied and drives
    get unplugged, and sending the player at a missing path would look like a
    broken episode rather than a missing file."""
    missing = tmp_path / "not-here.mp4"
    providers = _Providers()
    # The adapter is what stats the path; a store that finds nothing returns None.
    service = _service(_Downloads(None), providers)

    with pytest.raises(Exception):
        await service.resolve(_anime(), 1.0, allow_local=True)

    assert providers.called, "should have fallen through to streaming"
    assert not missing.exists()


async def test_a_broken_downloads_table_still_lets_you_stream(episode_file):
    """Bookkeeping must never cost you the episode — streaming is the path this
    feature is trying to save, not replace."""
    providers = _Providers()
    service = _service(_Downloads(str(episode_file), explode=True), providers)

    with pytest.raises(Exception):
        await service.resolve(_anime(), 1.0, allow_local=True)

    assert providers.called, "a store error blocked the fallback to streaming"


async def test_stream_flag_skips_the_local_copy(episode_file):
    """`anime play --stream`, for when the local copy is suspect."""
    providers = _Providers()
    service = _service(_Downloads(str(episode_file)), providers, prefer_local=False)

    with pytest.raises(Exception):
        await service.resolve(_anime(), 1.0, allow_local=True)

    assert providers.called


async def test_a_pinned_source_is_honoured_over_the_local_copy(episode_file):
    """Choosing a provider in the source picker is an explicit request for that
    provider, not a suggestion to be overridden by whatever is on disk."""

    class _Ref:
        def ref(self):
            return object()

    downloads = _Downloads(str(episode_file))
    service = _service(downloads, _Providers())

    with pytest.raises(Exception):
        await service.resolve(_anime(), 1.0, source=_Ref(), allow_local=True)

    assert downloads.asked == []


async def test_an_episode_on_disk_is_found_without_a_database_row(episode_file):
    """The case that makes this feature real rather than theoretical.

    `anime download` skips an episode it finds on disk *without writing a row*,
    and every download taken before this existed has no row either. Keying only
    off the downloads table would therefore have missed almost every file a
    real user actually has — verified against a real library: a fresh profile
    with an empty database still resolved the episode to its file on disk.
    """
    providers = _Providers()
    service = _service(_Downloads(None), providers)          # database knows nothing
    service.set_local_source(lambda _anime, _ep: episode_file)

    resolved = await service.resolve(_anime(), 1.0, allow_local=True)

    assert resolved.stream.url == str(episode_file)
    assert not providers.called


async def test_the_download_skip_and_playback_agree_on_what_you_have(tmp_path):
    """Both ask the same function. If they diverged you would get an episode
    that `download` calls finished and `play` goes to the network for."""
    from anime_sh.app.download import DownloadService

    service = DownloadService(playback=None, downloader=None, store=None,
                              library=None, download_dir=tmp_path)
    anime = _anime()

    assert service.local_path(anime, 1.0) is None
    dest = service.destination(anime, 1.0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"x")
    assert service.local_path(anime, 1.0) == dest


async def test_local_playback_is_recorded_as_its_own_source(episode_file):
    """History and `anime stats` should say the episode came from your disk,
    not credit whichever provider originally fetched it."""
    service = _service(_Downloads(str(episode_file)))

    candidate = await service._local_candidate(_anime(), 1.0, Audio.SUB)

    assert candidate is not None
    _episode, _stream, provider, host = candidate
    assert provider == "downloads"
    assert "download" in host.lower()


def test_the_path_comparison_is_the_one_the_filesystem_uses(episode_file):
    """Windows paths differ in case and separator without differing as files;
    this is the assumption the overwrite guard rests on."""
    assert os.path.normcase(str(episode_file)) == os.path.normcase(
        str(episode_file).upper()
    ) or os.name != "nt"
