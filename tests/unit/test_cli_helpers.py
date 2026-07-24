"""Pure CLI formatting helpers."""

from __future__ import annotations

import asyncio

import pytest
import typer

from anime_sh.cli import main as cli_main
from anime_sh.cli.main import (
    _calendar, _ep_list, _match_list_entry, _parse_episode_spec, _seasonal, _trending,
)
from anime_sh.domain.errors import MetadataError, NoStreamsFound
from anime_sh.domain.models import Anime, AnimeId, ListEntry, Title

from .fakes import make_anime


def test_ep_list_collapses_contiguous_runs():
    assert _ep_list([1.0, 2.0, 3.0, 4.0]) == "1–4"


def test_ep_list_keeps_gaps_and_specials_explicit():
    assert _ep_list([1.0, 2.0, 3.0, 5.0, 13.5]) == "1–3, 5, 13.5"


def test_ep_list_single_episode():
    assert _ep_list([1.0]) == "1"


def test_parse_episode_spec_single():
    assert _parse_episode_spec("5") == [5.0]


def test_parse_episode_spec_range():
    assert _parse_episode_spec("1-12") == [float(n) for n in range(1, 13)]


def test_parse_episode_spec_list_and_range_mixed():
    assert _parse_episode_spec("1-3,5,8") == [1.0, 2.0, 3.0, 5.0, 8.0]


def test_parse_episode_spec_dedupes_and_keeps_order():
    assert _parse_episode_spec("3,1-3") == [3.0, 1.0, 2.0]


def test_parse_episode_spec_reversed_range_normalised():
    assert _parse_episode_spec("5-3") == [3.0, 4.0, 5.0]


def test_parse_episode_spec_float_special():
    assert _parse_episode_spec("13.5") == [13.5]


def test_parse_episode_spec_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_episode_spec("abc")


def _entry(anilist, romaji, english=None):
    return ListEntry(
        anime=Anime(id=AnimeId(anilist=anilist),
                    title=Title(romaji=romaji, english=english)),
        status="CURRENT", progress=0,
    )


def test_match_list_entry_prefers_exact_title_over_spinoff():
    entries = [
        _entry(154587, "Sousou no Frieren", "Frieren: Beyond Journey's End"),
        _entry(170068, "Sousou no Frieren: Marumaru no Mahou"),
    ]
    hit = _match_list_entry(entries, "Frieren: Beyond Journey's End")
    assert hit is not None and hit.anime.id.anilist == 154587


def test_match_list_entry_none_when_not_on_list():
    entries = [_entry(1, "One Piece")]
    assert _match_list_entry(entries, "Totally Different Show") is None


# -- browse commands degrade gracefully when AniList is unreachable ---------- #
class _DeadMetadata:
    """Every catalog call fails as if the network/AniList were down."""

    async def trending(self, *, limit=30):
        raise MetadataError("AniList request failed: connect refused")

    async def seasonal(self, season, year):
        raise MetadataError("AniList request failed: connect refused")

    async def airing_schedule(self, start, end):
        raise MetadataError("AniList request failed: connect refused")


class _FakeContainer:
    def __init__(self):
        self.metadata = _DeadMetadata()
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture
def dead_container(monkeypatch):
    fake = _FakeContainer()
    monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: fake)
    return fake


@pytest.mark.parametrize("run", [
    lambda: _trending(10, False),
    lambda: _seasonal(None, None, False),
    lambda: _calendar(7, False),
])
def test_browse_commands_exit_cleanly_when_metadata_down(dead_container, run):
    # A MetadataError must surface as a typer.Exit (friendly message + exit 2),
    # never as a raw traceback — and the container must still be closed.
    with pytest.raises(typer.Exit) as ei:
        asyncio.run(run())
    assert ei.value.exit_code == 2
    assert dead_container.closed


# -- batch download orchestration ------------------------------------------- #
class _FakePath:
    def __init__(self, exists: bool, s: str = "/dl/x.mp4"):
        self._exists, self._s = exists, s

    def exists(self) -> bool:
        return self._exists

    def __str__(self) -> str:
        return self._s


class _FakeDownloadSvc:
    def __init__(self, existing=(), fail_on=()):
        self._existing = set(existing)
        self._fail_on = set(fail_on)
        self.downloaded: list[float] = []

    def available(self) -> bool:
        return True

    def destination(self, anime, episode):
        return _FakePath(episode in self._existing, f"/dl/E{episode:g}.mp4")

    async def download(self, anime, episode, *, audio):
        if episode in self._fail_on:
            raise NoStreamsFound(f"ep {episode:g} boom")
        self.downloaded.append(episode)
        return _FakePath(True, f"/dl/E{episode:g}.mp4")


class _DlContainer:
    def __init__(self, dl):
        self.download = dl
        self.closed = False

    class _Search:
        async def best_match(self, query):
            return make_anime()

    search = _Search()

    async def aclose(self):
        self.closed = True


@pytest.fixture
def dl_container(monkeypatch):
    def factory(dl):
        container = _DlContainer(dl)
        monkeypatch.setattr(cli_main, "build_container", lambda *a, **k: container)
        return container
    return factory


def test_download_batch_downloads_each(dl_container):
    dl = _FakeDownloadSvc()
    dl_container(dl)
    asyncio.run(cli_main._download("frieren", "1-3", False, None))
    assert dl.downloaded == [1.0, 2.0, 3.0]


def test_download_batch_skips_already_downloaded(dl_container):
    dl = _FakeDownloadSvc(existing={2.0})
    dl_container(dl)
    asyncio.run(cli_main._download("x", "1-3", False, None))
    assert dl.downloaded == [1.0, 3.0]  # ep 2 was on disk → skipped


def test_download_batch_continues_past_a_failure(dl_container):
    dl = _FakeDownloadSvc(fail_on={2.0})
    dl_container(dl)
    asyncio.run(cli_main._download("x", "1-3", False, None))
    assert dl.downloaded == [1.0, 3.0]  # ep 2 failed but the batch went on


def test_download_all_failed_exits_nonzero(dl_container):
    dl = _FakeDownloadSvc(fail_on={1.0})
    dl_container(dl)
    with pytest.raises(typer.Exit) as ei:
        asyncio.run(cli_main._download("x", "1", False, None))
    assert ei.value.exit_code == 2
