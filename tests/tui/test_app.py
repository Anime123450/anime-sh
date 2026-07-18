"""Headless TUI tests via Textual's Pilot — no real terminal needed.

Drives the app with fake services and asserts on the widget tree: home
populates, search-as-you-type swaps in results, and selecting an episode calls
playback. This is how the TUI is verified in CI.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from textual.widgets import ListView

from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Format,
    ResumeItem,
    SearchResult,
    Title,
    WatchProgress,
)
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.screens.detail import DetailScreen


def _anime(anilist, title, eps=12):
    return Anime(id=AnimeId(anilist=anilist), title=Title(romaji=title),
                 format=Format.TV, episode_count=eps, year=2023)


class FakeSearch:
    async def search(self, query, *, limit=25):
        return [SearchResult(anime=_anime(1, "Frieren")),
                SearchResult(anime=_anime(2, "Fruits Basket"))]


class FakeMetadata:
    name = "fake"
    async def trending(self, *, limit=20):
        return [_anime(10, "One Piece", eps=1100), _anime(11, "Bleach")]


class FakeLibrary:
    async def continue_watching(self, *, limit=20):
        prog = WatchProgress(AnimeId(anilist=1), 5.0, 300, 1400,
                             datetime.now(timezone.utc))
        return [ResumeItem(anime=_anime(1, "Frieren"), progress=prog)]


class FakePlayback:
    def __init__(self):
        self.played = []
        self.available = []
    async def play_and_track(self, anime, number, *, audio=None, source=None):
        self.played.append((anime.id.anilist, number))
    async def available_episodes(self, anime, *, audio=None, source=None):
        return list(self.available)
    async def list_sources(self, anime, *, audio=None):
        return []
    def set_on_event(self, cb):
        self.on_event = cb


def _make_app():
    playback = FakePlayback()
    services = TuiServices(
        search=FakeSearch(), metadata=FakeMetadata(),
        library=FakeLibrary(), playback=playback,
        aclose=_noop,
    )
    return AnimeShApp(services, theme="tokyo-night"), playback


async def _noop():
    return None


async def test_home_populates_trending_and_continue():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(app.query_one("#trending", ListView)) == 2
        assert len(app.query_one("#continue", ListView)) == 1


async def test_search_shows_results_and_hides_home():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#search").focus()
        await pilot.press(*"frieren")
        await pilot.pause(0.5)  # let the debounce timer + worker run
        await app.workers.wait_for_complete()
        await pilot.pause()
        results = app.query_one("#results", ListView)
        assert results.display is True
        assert len(results) == 2
        assert app.query_one("#trending").display is False


async def test_selecting_item_opens_detail():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        # Navigate: select the continue-watching item -> DetailScreen.
        cont = app.query_one("#continue", ListView)
        cont.focus()
        cont.index = 0
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert app.screen.anime.title.preferred == "Frieren"


async def test_selecting_episode_triggers_playback():
    app, playback = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = DetailScreen(_anime(1, "Frieren", eps=3))
        await app.push_screen(detail)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        episodes = detail.query_one("#episodes", ListView)
        assert len(episodes) == 3
        episodes.focus()
        episodes.index = 1
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert playback.played == [(1, 2.0)]


async def test_unavailable_episodes_stay_listed_but_inert():
    # Planned 3 eps, only 1-2 available: the full season stays visible, the
    # missing one is marked unavailable and selecting it does NOT play.
    app, playback = _make_app()
    playback.available = [1.0, 2.0]
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = DetailScreen(_anime(1, "Frieren", eps=3))
        await app.push_screen(detail)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        episodes = detail.query_one("#episodes", ListView)
        assert len(episodes) == 3  # not trimmed to the 2 available
        items = list(episodes.children)
        assert [it.available for it in items] == [True, True, False]
        assert "3/3" not in (detail.sub_title or "") and "2/3" in detail.sub_title
        episodes.focus()
        episodes.index = 2  # the unavailable one
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert playback.played == []  # inert, only a toast
