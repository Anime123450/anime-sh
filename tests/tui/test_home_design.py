"""The home screen's visual structure, asserted against a mounted screen.

Three changes made the screen look designed rather than dumped, and none of them
could be caught by a unit test — reverting each one on its own left the whole
suite green:

* the background tiers that give the rows a plate to sit on,
* one column grid shared by every section,
* row widths measured from the widget rather than computed from a constant.

All three are properties of the rendered screen, so all three are tested here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from textual.widgets import ListView

from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Format,
    Title,
    WatchProgress,
)
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.widgets import AnimeItem

from .test_app import FakePlayback, FakeSearch, ResumeItem, _noop


def _anime(anilist: int, title: str, eps: int = 12) -> Anime:
    # A cover URL on every show: the rail fetches one for the highlighted row,
    # and a fixture without one would make the debounce tests pass by having
    # nothing to debounce.
    return Anime(id=AnimeId(anilist=anilist), title=Title(romaji=title),
                 format=Format.TV, episode_count=eps, year=2023,
                 cover_url=f"https://example.invalid/{anilist}.jpg")


# Deliberately lopsided: Continue Watching holds the long titles and Seasonal
# the short ones. With each list sizing itself, that is exactly the shape that
# put their episode columns at different places on the screen.
_LONG = [
    "Rich Girl Caretaker: I'm Secretly the Caregiver of the Most Popular Girl",
    "The Duke's Son Claims He Won't Love Me Yet Showers Me with Adoration",
    "That Time I Got Reincarnated as a Slime Season 4",
    "Skeleton Knight in Another World Season 2",
]
_SHORT = ["BLACK TORCH", "Goodbye, Lara", "Sparks of Tomorrow", "Victoria"]


class _Library:
    async def continue_watching(self, *, limit=20):
        out = []
        for i, name in enumerate(_LONG, start=1):
            prog = WatchProgress(AnimeId(anilist=i), 3.0, 300, 1400,
                                 datetime.now(timezone.utc))
            out.append(ResumeItem(anime=_anime(i, name), progress=prog))
        return out

    async def progress_for(self, anime_id):
        return []

    async def favorites(self):
        return []


class _Metadata:
    name = "fake"

    async def trending(self, *, limit=20):
        return [_anime(90 + i, n) for i, n in enumerate(_SHORT)]

    async def seasonal(self, season, year):
        return [_anime(50 + i, n) for i, n in enumerate(_SHORT)]

    async def sequel(self, anime_id):
        return None


def _app() -> AnimeShApp:
    services = TuiServices(search=FakeSearch(), metadata=_Metadata(),
                           library=_Library(), playback=FakePlayback(),
                           aclose=_noop)
    return AnimeShApp(services, theme="tokyo-night")


async def _settle(app, pilot) -> None:
    """Let the screen finish. The grid re-cuts itself one refresh behind the
    layout, so a single pause reads a screen that is still moving."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    for _ in range(4):
        await pilot.pause()


async def test_the_rows_sit_on_a_plate_above_the_background():
    """Depth comes from background tiers, not from boxes — a border costs two
    terminal rows per section to say what a shade already says.

    The stylesheet used to set `Screen` to `$surface`, the *middle* tier, which
    left nowhere to go up: rows, rail and background were one colour and the
    screen read as a flat wall of text. Asserted as "these are different", not
    as a hex value, so a theme change cannot break it.
    """
    app = _app()
    async with app.run_test(size=(200, 44)) as pilot:
        await _settle(app, pilot)
        base = app.screen.styles.background
        plate = app.screen.query_one("#continue", ListView).styles.background
        rail = app.screen.query_one("#rail").styles.background

        assert plate != base, "the rows sit on the same colour as the background"
        assert rail != base, "the rail is not a surface, just a strip of background"
        assert plate.a and rail.a, "a transparent plate is no plate at all"


async def test_every_section_shares_one_column_grid():
    """The regression this file exists for. Each list sizing itself to its own
    titles put Continue Watching's episode column at 76 and Seasonal's at 70,
    with Trending somewhere else again — three grids down one screen, so the eye
    had no vertical line to follow.
    """
    app = _app()
    async with app.run_test(size=(200, 44)) as pilot:
        await _settle(app, pilot)
        grids = {
            item.parent.id: item._cols
            for item in app.screen.query(AnimeItem)
        }
        assert len(grids) >= 2, f"not enough sections to compare: {list(grids)}"
        assert len(set(grids.values())) == 1, (
            "sections are laid out on different grids: "
            + ", ".join(f"{k}={v.title}" for k, v in grids.items())
        )


async def test_no_row_is_wider_than_the_widget_drawing_it():
    """Row widths are measured from a mounted row, not computed from a constant.

    The constant could not be right: the paddings live in `app.tcss` where the
    geometry cannot see them, and the scrollbar comes and goes with the content,
    so a long list and a short one get different room. When it was wrong, the
    row overflowed its label, Textual wrapped the overflow to a second line, and
    `height: 1` hid it — "new episode" rendered as "new".
    """
    for width in (100, 120, 160, 200):
        app = _app()
        async with app.run_test(size=(width, 44)) as pilot:
            await _settle(app, pilot)
            for item in app.screen.query(AnimeItem):
                room = item.content_region.width
                if not room:
                    continue  # not laid out (a section still off-screen)
                assert item._cols.width <= room, (
                    f"at {width} columns, a {item._cols.width}-cell row is being "
                    f"drawn into {room} cells in #{item.parent.id}"
                )


async def test_the_focus_marker_costs_every_row_the_same_width():
    """The marker gutter is reserved on every row, so the focused row is not
    one cell narrower than its neighbours — otherwise the shared grid has to be
    cut to the smaller of the two, and the width flips as focus moves."""
    app = _app()
    async with app.run_test(size=(200, 44)) as pilot:
        await _settle(app, pilot)
        lv = app.screen.query_one("#continue", ListView)
        widths = {
            item.content_region.width
            for item in lv.children
            if isinstance(item, AnimeItem) and item.content_region.width
        }
        assert len(widths) == 1, f"rows in one list have differing widths: {widths}"


async def test_tab_moves_between_sections_and_nowhere_else():
    """Both the README and the `?` sheet say Tab means "next section", and
    Textual's default focus chain does not do that.

    Left alone it walks every focusable widget, which on this screen includes
    `#body` and `#rail` — scroll containers that accept focus, draw no cursor,
    and turn the arrow keys into panel scrolling. Measured before the fix, four
    presses went: continue → seasonal → trending → **rail** → **search** →
    **body**, so the documented behaviour was true for three presses and then
    quietly stopped being true.
    """
    from textual.widgets import ListView

    app = _app()
    async with app.run_test(size=(200, 44)) as pilot:
        await _settle(app, pilot)

        seen = []
        for _ in range(6):
            await pilot.press("tab")
            await pilot.pause()
            seen.append(app.focused)

        assert all(isinstance(w, ListView) for w in seen), (
            "Tab landed on something that is not a list: "
            + ", ".join(f"#{w.id}" if w is not None else "nothing" for w in seen)
        )
        ids = [w.id for w in seen]
        assert "body" not in ids and "rail" not in ids, f"Tab reached a container: {ids}"


async def test_tab_wraps_round_rather_than_stopping():
    """A cycle that dead-ends at the last section makes the key feel broken."""
    app = _app()
    async with app.run_test(size=(200, 44)) as pilot:
        await _settle(app, pilot)
        first = app.focused.id
        seen = set()
        for _ in range(8):
            await pilot.press("tab")
            await pilot.pause()
            seen.add(app.focused.id)
        assert first in seen, "Tab never came back round to where it started"


async def test_shift_tab_goes_the_other_way():
    app = _app()
    async with app.run_test(size=(200, 44)) as pilot:
        await _settle(app, pilot)
        start = app.focused.id
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused.id == start


async def test_tab_skips_a_section_with_nothing_in_it():
    """Favorites stays empty until you star something, and searching hides the
    browse lists. Landing on either is landing nowhere."""
    from textual.widgets import ListView

    app = _app()
    async with app.run_test(size=(200, 44)) as pilot:
        await _settle(app, pilot)
        empty = [lv.id for lv in app.screen.query(ListView)
                 if not len(lv.children) or not lv.display]
        assert empty, "test premise: no empty or hidden list to skip"

        for _ in range(6):
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id not in empty, (
                f"Tab stopped on #{app.focused.id}, which has no rows"
            )


async def test_a_toast_does_not_land_on_top_of_the_rail():
    """Textual docks notifications bottom-*right*, which is exactly where the
    Coming Up rail lives. A toast is 60 cells wide and the rail is barely more
    than half that, so "Synced 74 from AniList" covered the next three days of
    schedule outright — and the rail is the one thing on screen you cannot
    scroll back to, because it is not a list.

    Asserted on the computed style rather than by screenshotting a toast:
    notifications are not mounted at all in headless mode, so a rendered check
    here would pass whether or not the rule applied.
    """
    from textual.widgets._toast import ToastRack

    from .test_app import _make_app

    app, _ = _make_app()
    async with app.run_test(size=(190, 44)) as pilot:
        await pilot.pause()
        rack = ToastRack()
        await app.screen.mount(rack)
        await pilot.pause()
        assert rack.styles.align_horizontal == "left", (
            "toasts are back on the right, over the rail"
        )
        assert rack.styles.align_vertical == "bottom"
