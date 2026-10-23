"""The detail screen's two arrangements, and the optional source picker.

Wide (>= 150 columns): the show on the left, the episodes on the right, each
filling the height between the bars. The page itself never scrolls, so the
poster and the top bar stay put however far the cursor travels.

Narrow: stacked, with the episodes taking the height the show leaves over and
scrolling inside it.

The source picker is something you open with `s`, not a page you pass through.
"""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from textual.widgets import ListView

import anime_sh.tui.screens.detail as detail_mod
from anime_sh.domain.models import Anime, AnimeId, Audio, SourceOption, Title
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui import coverart
from anime_sh.tui.screens.detail import DetailScreen, series_line
from anime_sh.tui.screens.sources import SourcesScreen
from anime_sh.tui.widgets import SourceItem

from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _anime, _make_app, _noop


@pytest.fixture
def layout(monkeypatch):
    def _use(name: str):
        monkeypatch.setattr(
            DetailScreen, "_configured_layout", staticmethod(lambda: name))
    return _use


@pytest.fixture(autouse=True)
def poster(monkeypatch):
    """A cover, so the poster column is real rather than collapsed."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (120, 170), (60, 40, 90)).save(buf, format="PNG")
    data = buf.getvalue()
    monkeypatch.setattr(coverart, "cached_cover", lambda url: data)


def _show(eps=1175, **kw):
    return replace(_anime(1, "ONE PIECE", eps=eps),
                   cover_url="https://example.invalid/cover/1.png", **kw)


async def _open(app, pilot, show):
    d = DetailScreen(show)
    await app.push_screen(d)
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    return d


async def _walk_down(pilot, n):
    for _ in range(n):
        await pilot.press("down")
    await pilot.pause()


@pytest.mark.parametrize("name", ["list", "grid"])
async def test_wide_is_two_columns_and_the_top_survives_scrolling(layout, name):
    layout(name)
    app, _ = _make_app()
    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show())
        assert d.has_class("-wide")

        left, right = d.query_one("#detail-left"), d.query_one("#detail-right")
        assert left.region.x < right.region.x, "episodes are not beside the show"
        assert left.region.width <= 100
        # The episodes use the rest of the width and the full height.
        assert right.region.right >= 186
        assert right.region.height >= 40

        cover = d.query_one("#detail-cover").region
        info = d.query_one("#detail-info").region
        assert cover.y == info.y, "poster is not top-aligned with the info"
        before = (cover.y, d.query_one("#topbar").region.y)

        await _walk_down(pilot, 40)
        grid = d.query_one("#episodes", ListView)
        assert grid.index and grid.index >= 40
        assert d.query_one("#detail-body").scroll_y == 0
        assert d.scroll_y == 0
        assert (d.query_one("#detail-cover").region.y,
                d.query_one("#topbar").region.y) == before


async def test_wide_grid_columns_come_from_the_right_column_not_the_screen(layout):
    layout("grid")
    app, _ = _make_app()
    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show())
        right = d.query_one("#detail-right").region.width
        cols = d._episode_columns()
        assert cols * d._cell_width() <= right
        assert cols < 190 // d._cell_width(), "sized from the screen width"


@pytest.mark.parametrize("size", [(120, 35), (80, 24)])
async def test_narrow_is_stacked_and_the_episodes_scroll_inside(layout, size):
    layout("list")
    app, _ = _make_app()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show())
        assert not d.has_class("-wide")

        left, right = d.query_one("#detail-left"), d.query_one("#detail-right")
        assert right.region.y >= left.region.bottom, "episodes overlap the show"
        # Nothing runs under the action bar.
        assert right.region.bottom <= d.query_one("#actionbar").region.y
        assert d.query_one("#episodes").region.height >= 6

        top = d.query_one("#detail-cover").region.y
        await _walk_down(pilot, 30)
        assert d.query_one("#detail-cover").region.y == top
        assert d.query_one("#detail-body").scroll_y == 0


async def test_resizing_switches_between_the_arrangements(layout):
    layout("list")
    app, _ = _make_app()
    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show())
        assert d.has_class("-wide")
        await pilot.resize_terminal(100, 40)
        await pilot.pause()
        assert not d.has_class("-wide")
        await pilot.resize_terminal(190, 49)
        await pilot.pause()
        assert d.has_class("-wide")


async def test_synopsis_is_cleaned_and_has_no_blank_gaps():
    app, _ = _make_app()
    synopsis = ("First paragraph.<br><br>Second paragraph, still story. "
                "(Source: Anime News Network)\n\nNotes:\n- The first novel.")
    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show(eps=12, synopsis=synopsis))
        text = d._header_text()
        assert "Source" not in text and "Notes" not in text
        assert "First paragraph. Second paragraph, still story." in text
        assert "\n\n\n" not in text


async def test_long_synopsis_is_cut_with_an_ellipsis():
    app, _ = _make_app()
    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show(eps=12, synopsis="word " * 600))
        block = d._header_text().split("\n\n", 1)[1]
        assert block.endswith("…")
        assert len(block.splitlines()) <= 12


def test_series_line_counts_finished_episodes_and_says_nothing_of_a_percent():
    line = series_line(0, 25, 24)
    assert "0 of 25 watched" in line and "25 left" in line and "~10h" in line
    assert "%" not in line


async def test_action_button_carries_the_in_episode_percent_and_nothing_else_does():
    from datetime import datetime, timezone

    from anime_sh.domain.models import WatchProgress

    library = FakeLibrary()
    library.progress = [WatchProgress(AnimeId(anilist=1), 1.0, 14, 1400,
                                      datetime.now(timezone.utc), completed=False)]
    services = TuiServices(search=FakeSearch(), metadata=FakeMetadata(),
                           library=library, playback=FakePlayback(), aclose=_noop)
    app = AnimeShApp(services, theme="tokyo-night")
    async with app.run_test(size=(190, 49)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show(eps=25))
        action = str(d.query_one("#detail-action").render())
        progress = str(d.query_one("#detail-progress").render())
        assert "resume episode 1 · 1%" in action
        assert "% watched" not in action
        assert "0 of 25 watched" in progress and "%" not in progress


# -- the source picker is optional, and explicit -------------------------------


class _Sources(FakePlayback):
    def __init__(self):
        super().__init__()
        self.pinned: list = []
        self.options = [
            SourceOption("hianime", "a", "Long Series", 12, Audio.SUB, 1.0),
            SourceOption("anikoto", "b", "Long Series [Mini]", 4, Audio.SUB, 0.8),
        ]

    async def list_sources(self, anime, *, audio=None):
        return self.options

    async def available_episodes(self, anime, *, audio=None, source=None):
        self.pinned.append(source)
        return [1.0, 2.0] if source is not None else []


def _picker_app():
    playback = _Sources()
    services = TuiServices(search=FakeSearch(), metadata=FakeMetadata(),
                           library=FakeLibrary(), playback=playback, aclose=_noop)
    return AnimeShApp(services, theme="tokyo-night"), playback


async def test_s_opens_a_picker_whose_choice_pins_the_source_without_a_second_detail():
    app, playback = _picker_app()
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show(eps=12))
        assert d.source is None
        await pilot.press("s")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, SourcesScreen)
        rows = app.screen.query_one("#sources", ListView).children
        sources = [r for r in rows if isinstance(r, SourceItem)]
        assert len(sources) == 2

        # Row 0 is "Automatic"; the second source is two steps down.
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.screen is d, "choosing pushed another screen"
        assert sum(isinstance(s, DetailScreen) for s in app.screen_stack) == 1
        assert d.source.provider == "anikoto"
        assert playback.pinned[-1].provider == "anikoto", "availability not refreshed"


async def test_escape_in_the_picker_changes_nothing():
    app, _ = _picker_app()
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show(eps=12))
        await pilot.press("s")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is d and d.source is None


async def test_picker_columns_line_up():
    app, _ = _picker_app()
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await _open(app, pilot, _show(eps=12))
        await pilot.press("s")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        rows = [r for r in app.screen.query(SourceItem)]
        plain = [str(r.query_one("Static").render()) for r in rows]
        assert len({p.index("hianime" if "hianime" in p else "anikoto") for p in plain}) == 1
        assert app.screen.query_one("#sources").region.height > 6


async def test_a_dubless_title_offers_the_sub_fallback_from_the_detail_screen():
    class Dubless(_Sources):
        async def list_sources(self, anime, *, audio=None):
            return [] if audio is Audio.DUB else self.options

    playback = Dubless()
    services = TuiServices(search=FakeSearch(), metadata=FakeMetadata(),
                           library=FakeLibrary(), playback=playback, aclose=_noop)
    app = AnimeShApp(services, theme="tokyo-night", audio=Audio.DUB)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        d = await _open(app, pilot, _show(eps=12))
        await pilot.press("s")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "No English-dub source" in str(
            app.screen.query_one("#sources-status").render())
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is d
        assert d.audio is Audio.SUB and app.audio is Audio.DUB


async def test_next_season_opens_the_detail_screen_directly():
    sequel = _anime(2, "Frieren Season 2")

    class Meta(FakeMetadata):
        async def sequel(self, anime_id):
            return sequel

    services = TuiServices(search=FakeSearch(), metadata=Meta(),
                           library=FakeLibrary(), playback=FakePlayback(),
                           aclose=_noop)
    app = AnimeShApp(services, theme="tokyo-night")
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await _open(app, pilot, _show(eps=12))
        await pilot.press("n")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, DetailScreen)
        assert app.screen.anime.id.anilist == 2
        assert not any(isinstance(s, SourcesScreen) for s in app.screen_stack)
