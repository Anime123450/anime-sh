"""Where the keyboard is, and what it can do.

Two problems, both invisible to every existing test.

The app launched with focus on the search `Input`. `on_mount` did try to focus a
browse list, but at mount every list is still empty — focusing an empty ListView
does not stick, and the rows arrive later from workers that clear and rebuild the
list. The attempt sat inside a bare `try/except`, so the failure was silent and
the comment above it described behaviour the app did not have. In practice you
opened anime-sh, pressed the arrow keys, and nothing moved.

And the selection style never applied at all: Textual marks the current row with
`-highlight`, one dash, while the stylesheet said `--highlight`. A dead CSS
selector raises nothing and renders no complaint.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input, ListView

from anime_sh.tui import AnimeShApp, TuiServices

from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _noop


def _app() -> AnimeShApp:
    services = TuiServices(search=FakeSearch(), metadata=FakeMetadata(),
                           library=FakeLibrary(), playback=FakePlayback(),
                           aclose=_noop)
    return AnimeShApp(services, theme="tokyo-night")


async def _settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_the_keyboard_starts_on_a_list_not_the_search_box():
    """The regression test. Focus began on the Input, so arrows did nothing."""
    app = _app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)

        assert isinstance(app.focused, ListView), (
            f"focus landed on {type(app.focused).__name__}, not a list"
        )
        assert len(app.focused.children), "focused an empty list"


async def test_focus_settles_on_continue_watching_every_launch():
    """Sections finish loading in whatever order their workers return, so
    claiming the first to arrive put focus somewhere different each time. A
    layout you cannot build a habit around is worse than one you dislike."""
    for _ in range(3):
        app = _app()
        async with app.run_test() as pilot:
            await _settle(app, pilot)
            assert app.focused.id == "continue", app.focused.id


async def test_a_row_is_actually_selected_on_launch():
    """A focused list with `index is None` shows no cursor, and the first arrow
    press moves *to* the first row rather than off it. Continue Watching paints
    twice — cached rows, then enriched — and the rebuild resets the index, so
    this needs re-asserting after the second paint, not just the first."""
    app = _app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)

        assert app.focused.index == 0
        highlighted = [c for c in app.focused.children if c.has_class("-highlight")]
        assert len(highlighted) == 1, "no row is showing a cursor"


async def test_the_focused_list_looks_different_from_the_others():
    """Five lists each keep their place, so without this the screen shows several
    identical-looking cursors and no clue which one the keyboard drives.

    Asserts the computed style, because the bug being guarded against was a
    selector that matched nothing — a rule that exists in the file proves
    nothing about whether it applied.
    """
    app = _app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        focused = app.focused
        other = app.screen.query_one("#trending", ListView)
        other.index = 0
        await pilot.pause()

        def cursor(lv):
            return next(c for c in lv.children if c.has_class("-highlight"))

        near = cursor(focused).styles
        far = cursor(other).styles

        # Measured as separation from the plate the rows sit on, not as alpha.
        # Alpha was standing in for "stronger" and stopped meaning that the
        # moment the unfocused cursor became an opaque colour a tier up: it
        # scored 1.0 against the focused cursor's 0.4 while being, on screen,
        # much the fainter of the two.
        plate = focused.styles.background

        def against_plate(styles):
            bg = styles.background
            solid = bg if bg.a == 1 else plate.blend(bg, bg.a)
            return (abs(solid.r - plate.r) + abs(solid.g - plate.g)
                    + abs(solid.b - plate.b))

        assert against_plate(near) > against_plate(far), (
            "the focused list's cursor is no stronger than an unfocused one"
        )
        # Shape as well as colour, so the distinction survives a monochrome
        # terminal — colour must reinforce hierarchy, never carry it alone.
        assert near.border_left[0] and not far.border_left[0]


async def test_vim_motions_move_the_cursor():
    """j/k/g/G are the first things a terminal user reaches for."""
    app = _app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        lv = app.focused
        rows = len(lv.children)
        if rows < 2:
            lv = app.screen.query_one("#trending", ListView)
            lv.focus()
            lv.index = 0
            await pilot.pause()
            rows = len(lv.children)
        assert rows >= 2, "fixture needs at least two rows to move between"

        await pilot.press("j")
        await pilot.pause()
        assert lv.index == 1

        await pilot.press("k")
        await pilot.pause()
        assert lv.index == 0

        await pilot.press("G")
        await pilot.pause()
        assert lv.index == rows - 1

        await pilot.press("g")
        await pilot.pause()
        assert lv.index == 0


async def test_typing_j_into_the_search_box_types_a_j():
    """The motions are bound on the screen rather than globally for exactly this
    reason: binding them app-wide would make the search box unusable for every
    title containing one of those letters."""
    app = _app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("j", "o", "j", "o")
        await pilot.pause()

        assert app.screen.query_one("#search", Input).value == "jojo"


async def test_focus_is_not_stolen_after_you_have_moved():
    """Sections keep arriving after launch. Once you have chosen a list, a late
    loader must not yank the keyboard back."""
    app = _app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)

        chosen = app.screen.query_one("#trending", ListView)
        chosen.focus()
        await pilot.pause()

        # Re-run the hook every late section calls.
        app.screen._set_section("#sec-seasonal", "Airing This Season", 2)
        await pilot.pause()

        assert app.focused is chosen, "a late section stole the keyboard"
