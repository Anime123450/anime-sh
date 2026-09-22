"""`anime prefetch` — stock the next episodes before you need them.

A downloaded episode needs no provider, no resolver and no network, which is
exactly what you want on a train and on the days a CDN is down. The choice of
*which* episode is the part worth pinning down.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
import typer

from anime_sh.cli import main as cli_main
from anime_sh.cli.main import next_unwatched
from anime_sh.domain.errors import ProviderError
from anime_sh.domain.models import (
    Anime,
    AnimeId,
    ResumeItem,
    Title,
    WatchProgress,
)

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def _progress(episode: float, *, completed: bool) -> WatchProgress:
    return WatchProgress(
        anime_id=AnimeId(anilist=1), episode=episode, position_s=300,
        duration_s=1400, updated_at=NOW, completed=completed,
    )


# -- which episode is "next" ------------------------------------------------ #
def test_a_part_watched_episode_is_the_next_one_you_want():
    """You stopped in the middle of it — that is the episode to have on disk,
    not the one after it."""
    assert next_unwatched(_progress(8, completed=False), 12) == 8


def test_after_finishing_one_next_means_the_one_after():
    assert next_unwatched(_progress(8, completed=True), 12) == 9


def test_past_the_end_of_a_finished_show_there_is_nothing_to_stock():
    assert next_unwatched(_progress(12, completed=True), 12) is None


def test_an_unknown_length_never_blocks_stocking():
    """Ongoing and unannounced seasons have no episode count; that must not stop
    the next episode being fetched."""
    assert next_unwatched(_progress(12, completed=True), None) == 13


# -- the command ------------------------------------------------------------ #
def _anime(title: str, episodes: int | None) -> Anime:
    return Anime(id=AnimeId(anilist=1), title=Title(romaji=title), episode_count=episodes)


class _Downloads:
    def __init__(self, *, have=(), fail_on=(), available=True):
        self.have = set(have)
        self.fail_on = set(fail_on)
        self._available = available
        self.fetched: list[float] = []

    def available(self):
        return self._available

    def local_path(self, anime, n):
        return "/on/disk.mp4" if n in self.have else None

    async def download(self, anime, n, *, audio):
        if n in self.fail_on:
            raise ProviderError("host down")
        self.fetched.append(n)
        return f"/dl/{n:g}.mp4"


class _LibraryService:
    def __init__(self, items):
        self.items = items

    async def continue_watching(self, *, limit=20):
        return self.items[:limit]


class _Container:
    def __init__(self, items, downloads):
        self.library_service = _LibraryService(items)
        self.download = downloads

    async def aclose(self):
        pass


@pytest.fixture
def run(monkeypatch):
    def go(items, downloads, *, count=1, shows=5, dry_run=False):
        monkeypatch.setattr(
            cli_main, "build_container",
            lambda *a, **k: _Container(items, downloads),
        )
        return asyncio.run(cli_main._prefetch(count, shows, False, dry_run))

    return go


def test_fetches_the_next_episode_of_each_show(run):
    items = [
        ResumeItem(anime=_anime("A", 12), progress=_progress(3, completed=True)),
        ResumeItem(anime=_anime("B", 12), progress=_progress(7, completed=False)),
    ]
    downloads = _Downloads()
    run(items, downloads)
    assert downloads.fetched == [4.0, 7.0]


def test_count_stocks_several_without_running_past_the_finale(run):
    items = [ResumeItem(anime=_anime("A", 12), progress=_progress(11, completed=True))]
    downloads = _Downloads()
    run(items, downloads, count=5)
    assert downloads.fetched == [12.0], "only episode 12 exists"


def test_episodes_already_on_disk_are_skipped(run):
    """Running it twice must cost nothing — that is what makes it safe to put in
    a shell alias."""
    items = [ResumeItem(anime=_anime("A", 12), progress=_progress(3, completed=True))]
    downloads = _Downloads(have={4.0})
    run(items, downloads, count=2)
    assert downloads.fetched == [5.0]


def test_one_shows_provider_being_down_does_not_cost_the_others(run):
    items = [
        ResumeItem(anime=_anime("A", 12), progress=_progress(1, completed=True)),
        ResumeItem(anime=_anime("B", 12), progress=_progress(5, completed=True)),
    ]
    downloads = _Downloads(fail_on={2.0})
    run(items, downloads)
    assert downloads.fetched == [6.0]


def test_dry_run_downloads_nothing(run):
    items = [ResumeItem(anime=_anime("A", 12), progress=_progress(3, completed=True))]
    downloads = _Downloads()
    run(items, downloads, dry_run=True)
    assert downloads.fetched == []


def test_dry_run_works_without_ffmpeg(run):
    """Asking what *would* be fetched is a question about your library, not
    about your codecs."""
    items = [ResumeItem(anime=_anime("A", 12), progress=_progress(3, completed=True))]
    run(items, _Downloads(available=False), dry_run=True)


def test_missing_ffmpeg_stops_a_real_run(run):
    items = [ResumeItem(anime=_anime("A", 12), progress=_progress(3, completed=True))]
    with pytest.raises(typer.Exit) as ei:
        run(items, _Downloads(available=False))
    assert ei.value.exit_code == 1


def test_an_empty_continue_list_is_not_an_error(run):
    downloads = _Downloads()
    run([], downloads)
    assert downloads.fetched == []


def test_a_finished_show_is_not_offered_a_nonexistent_episode(run):
    items = [ResumeItem(anime=_anime("A", 12), progress=_progress(12, completed=True))]
    downloads = _Downloads()
    run(items, downloads)
    assert downloads.fetched == []


# -- episodes that have not aired yet ---------------------------------------- #
def _airing(title: str, episodes: int | None, next_ep: int | None) -> Anime:
    return Anime(
        id=AnimeId(anilist=1), title=Title(romaji=title),
        episode_count=episodes, next_airing_episode=next_ep,
    )


def test_has_aired_reads_next_airing_as_the_first_unaired_one():
    from anime_sh.cli.main import has_aired

    show = _airing("A", 12, 9)          # eps 1-8 exist, 9 is next week's
    assert has_aired(show, 8)
    assert not has_aired(show, 9)
    assert not has_aired(show, 10)


def test_has_aired_is_true_when_nothing_is_scheduled():
    """A finished show has no next_airing_episode, and guessing "not aired"
    there would refuse to fetch anything at all."""
    from anime_sh.cli.main import has_aired

    assert has_aired(_airing("A", 12, None), 12)


def test_a_caught_up_show_is_waiting_not_failing(run):
    """Continue Watching deliberately keeps shows you are up to date on — that
    is how it tells you what you are waiting for. Asking a provider for next
    week's episode fails, and counting that as a failure made a fully caught-up
    library report nothing but errors."""
    items = [
        ResumeItem(anime=_airing("A", 12, 9), progress=_progress(8, completed=True)),
    ]
    downloads = _Downloads()
    run(items, downloads)
    assert downloads.fetched == [], "episode 9 has not aired"


def test_being_caught_up_on_everything_still_exits_zero(run):
    """Otherwise `anime prefetch` in a cron entry or a shell alias fails on the
    days when there is simply nothing new."""
    items = [
        ResumeItem(anime=_airing("A", 12, 9), progress=_progress(8, completed=True)),
        ResumeItem(anime=_airing("B", 24, 5), progress=_progress(4, completed=True)),
    ]
    run(items, _Downloads())  # no typer.Exit raised


def test_aired_episodes_are_still_fetched_on_an_airing_show(run):
    items = [
        ResumeItem(anime=_airing("A", 12, 9), progress=_progress(5, completed=True)),
    ]
    downloads = _Downloads()
    run(items, downloads, count=5)
    # 6, 7, 8 exist; 9 and 10 have not aired.
    assert downloads.fetched == [6.0, 7.0, 8.0]
