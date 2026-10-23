"""Tests for the Franchise panel on DetailScreen.

Verifies:
- Panel mounted and visible in wide mode (>= 150 cols), occupies space below info
- Hidden / compact in narrow mode (< 150 cols)
- Tab switches focus between episodes list and franchise list
- Enter on a franchise row pushes DetailScreen for that entry (or focuses episodes if current)
- w on franchise row opens confirmation and marks entry watched
- w on episode row opens confirmation and marks up to episode watched
- Graceful fallback when FranchiseService is None or raises
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from textual.widgets import ListView

from anime_sh.app.franchise import EntryStatus, EpisodeInfo, Franchise, FranchiseEntry
from anime_sh.domain.models import Anime, AnimeId, Format
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.franchise_panel import FranchiseItem, FranchisePanel
from anime_sh.tui.screens.confirm import ConfirmScreen
from anime_sh.tui.screens.detail import DetailScreen
from anime_sh.tui.widgets import EpisodeItem

from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _anime, _noop


class FakeFranchiseService:
    def __init__(self, franchise: Franchise | None = None, ep_info: dict[int, EpisodeInfo] | None = None) -> None:
        self._franchise = franchise
        self._ep_info = ep_info or {}
        self.marked_entries: list[Anime] = []

    async def build(self, anime: Anime) -> Franchise:
        if self._franchise is not None:
            return self._franchise
        # Default simple 2-entry franchise
        e1 = FranchiseEntry(
            anime=anime, kind="season", main_line=True,
            status=EntryStatus.NOT_STARTED, watched=0, total=anime.episode_count,
            is_current=True,
        )
        other = replace(_anime(2, "Re:ZERO Season 2", eps=13), format=Format.TV)
        e2 = FranchiseEntry(
            anime=other, kind="season", main_line=True,
            status=EntryStatus.NOT_STARTED, watched=0, total=13,
            is_current=False,
        )
        return Franchise(entries=(e1, e2), current_index=0, suggested_index=1)

    async def mark_entry_watched(self, anime: Anime) -> int:
        self.marked_entries.append(anime)
        return anime.episode_count or 12

    async def episode_info(self, anime: Anime) -> dict[int, EpisodeInfo]:
        return self._ep_info


def _make_franchise_app(franchise_svc=None, library=None):
    lib = library or FakeLibrary()
    meta = FakeMetadata()
    services = TuiServices(
        search=FakeSearch(),
        metadata=meta,
        library=lib,
        playback=FakePlayback(),
        aclose=_noop,
        franchise=franchise_svc,
    )
    return AnimeShApp(services, theme="tokyo-night")


async def _open_detail(app, pilot, show):
    d = DetailScreen(show)
    await app.push_screen(d)
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return d


async def test_franchise_panel_mounted_and_visible_in_wide():
    s1 = _anime(1, "Re:ZERO S1", eps=25)
    s2 = _anime(2, "Re:ZERO S2", eps=13)
    f = Franchise(
        entries=(
            FranchiseEntry(s1, "season", True, EntryStatus.COMPLETED, 25, 25, is_current=True),
            FranchiseEntry(s2, "season", True, EntryStatus.NOT_STARTED, 0, 13, is_current=False),
        ),
        current_index=0,
        suggested_index=1,
    )
    svc = FakeFranchiseService(franchise=f)
    app = _make_franchise_app(franchise_svc=svc)

    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open_detail(app, pilot, s1)
        assert d.has_class("-wide")
        fp = d.query_one("#detail-franchise", FranchisePanel)
        assert fp.is_mounted
        assert fp.display is True
        assert fp.region.height >= 8

        lv = fp.query_one("#franchise-list", ListView)
        assert len(lv.children) == 2
        item0, item1 = lv.children[0], lv.children[1]
        assert isinstance(item0, FranchiseItem)
        assert item0.has_class("-current")
        assert "✓" in item0._format_text()
        assert "→ up next" in item1._format_text()


async def test_franchise_panel_hidden_in_narrow():
    s1 = _anime(1, "Re:ZERO S1", eps=25)
    svc = FakeFranchiseService()
    app = _make_franchise_app(franchise_svc=svc)

    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        d = await _open_detail(app, pilot, s1)
        assert not d.has_class("-wide")
        fp = d.query_one("#detail-franchise", FranchisePanel)
        assert fp.display is False


async def test_tab_toggles_focus_between_episodes_and_franchise():
    s1 = _anime(1, "Re:ZERO S1", eps=25)
    svc = FakeFranchiseService()
    app = _make_franchise_app(franchise_svc=svc)

    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open_detail(app, pilot, s1)
        ep_lv = d.query_one("#episodes", ListView)
        fp_lv = d.query_one("#franchise-list", ListView)

        # Focus starts on episodes (or body)
        ep_lv.focus()
        assert ep_lv.has_focus
        assert not fp_lv.has_focus

        # Tab to franchise
        await pilot.press("tab")
        await pilot.pause()
        assert fp_lv.has_focus
        assert not ep_lv.has_focus

        # Tab back to episodes
        await pilot.press("tab")
        await pilot.pause()
        assert ep_lv.has_focus
        assert not fp_lv.has_focus


async def test_enter_on_franchise_row_pushes_detail():
    s1 = _anime(1, "Re:ZERO S1", eps=25)
    s2 = _anime(2, "Re:ZERO S2", eps=13)
    f = Franchise(
        entries=(
            FranchiseEntry(s1, "season", True, EntryStatus.COMPLETED, 25, 25, is_current=True),
            FranchiseEntry(s2, "season", True, EntryStatus.NOT_STARTED, 0, 13, is_current=False),
        ),
        current_index=0,
        suggested_index=1,
    )
    svc = FakeFranchiseService(franchise=f)
    app = _make_franchise_app(franchise_svc=svc)

    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open_detail(app, pilot, s1)
        fp_lv = d.query_one("#franchise-list", ListView)
        fp_lv.focus()
        fp_lv.index = 1  # Select S2
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, DetailScreen)
        assert app.screen.anime.id == s2.id


async def test_w_on_franchise_row_confirms_and_marks_watched():
    s1 = _anime(1, "Re:ZERO S1", eps=25)
    s2 = _anime(2, "Re:ZERO S2", eps=13)
    f = Franchise(
        entries=(
            FranchiseEntry(s1, "season", True, EntryStatus.NOT_STARTED, 0, 25, is_current=True),
            FranchiseEntry(s2, "season", True, EntryStatus.NOT_STARTED, 0, 13, is_current=False),
        ),
        current_index=0,
        suggested_index=0,
    )
    svc = FakeFranchiseService(franchise=f)
    app = _make_franchise_app(franchise_svc=svc)

    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open_detail(app, pilot, s1)
        fp_lv = d.query_one("#franchise-list", ListView)
        fp_lv.focus()
        fp_lv.index = 1  # highlight S2
        await pilot.press("w")
        await pilot.pause()

        # ConfirmScreen should be pushed
        assert isinstance(app.screen, ConfirmScreen)
        # Confirm
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.screen is d
        assert len(svc.marked_entries) == 1
        assert svc.marked_entries[0].id == s2.id


async def test_w_on_episode_row_confirms_and_marks_up_to_number():
    s1 = _anime(1, "Re:ZERO S1", eps=25)
    lib = FakeLibrary()
    app = _make_franchise_app(library=lib)

    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open_detail(app, pilot, s1)
        ep_lv = d.query_one("#episodes", ListView)
        ep_lv.focus()
        ep_lv.index = 4  # episode 5
        await pilot.press("w")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.screen is d
        # Verify library received mark_watched up to 5
        assert lib.marks == [(s1.id, 5.0)]


async def test_service_none_or_failing_renders_without_error():
    class FailingFranchiseService:
        async def build(self, anime):
            raise RuntimeError("Network down")
        async def episode_info(self, anime):
            raise RuntimeError("Network down")

    s1 = _anime(1, "Re:ZERO S1", eps=25)
    app = _make_franchise_app(franchise_svc=FailingFranchiseService())

    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open_detail(app, pilot, s1)
        assert d.has_class("-wide")
        # Screen renders fine, episodes list is present
        ep_lv = d.query_one("#episodes", ListView)
        assert len(ep_lv.children) == 25
