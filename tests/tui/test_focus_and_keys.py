"""Where the keyboard is, and what it can do.

Two problems, both invisible to every existing test.

The app launched with focus on the search `Input`. `on_mount` did try to focus a
browse shelf, but at mount every shelf is still empty — focusing an empty
container does not stick, and the cards arrive later from workers that clear and
rebuild it. The attempt sat inside a bare `try/except`, so the failure was silent
and the comment above it described behaviour the app did not have. In practice
you opened anime-sh, pressed the arrow keys, and nothing moved.

And the selection style never applied at all: the stylesheet named the wrong
class. A dead CSS selector raises nothing and renders no complaint — which is
why the checks below read the computed style rather than the file.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input

from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.cards import Shelf

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


async def test_the_keyboard_starts_on_a_shelf_not_the_search_box():
    """The regression test. Focus began on the Input, so arrows did nothing."""
    app = _app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)

        assert isinstance(app.focused, Shelf), (
            f"focus landed on {type(app.focused).__name__}, not a shelf"
        )
        assert app.focused.cards, "focused an empty shelf"


async def test_focus_settles_on_continue_watching_every_launch():
    """Shelves finish loading in whatever order their workers return, so
    claiming the first to arrive put focus somewhere different each time. A
    layout you cannot build a habit around is worse than one you dislike."""
    for _ in range(3):
        app = _app()
        async with app.run_test() as pilot:
            await _settle(app, pilot)
            assert app.focused.id == "continue", app.focused.id


async def test_a_card_is_actually_selected_on_launch():
    """A focused shelf showing no cursor leaves the first arrow press moving
    *to* the first card rather than off it. Continue Watching paints twice —
    cached rows, then enriched — and the rebuild resets the index, so this needs
    re-asserting after the second paint, not just the first."""
    app = _app()
    async with app.run_test() as pilot:
        await _settle(app, pilot)

        assert app.focused.index == 0
        marked = [c for c in app.focused.cards if c.selected]
        assert len(marked) == 1, "no card is showing a cursor"
        assert marked[0] is app.focused.cards[0]


async def test_only_the_focused_shelf_lifts_its_selected_card():
    """Every shelf keeps its place so you do not lose it moving between them,
    but only one of them is live.

    Without this the screen shows several identical-looking cursors and no clue
    which one the keyboard drives. Asserted on the computed style, because the
    bug being guarded against was a selector that matched nothing — a rule that
    exists in the file proves nothing about whether it applied.
    """
    app = _app()
    async with app.run_test(size=(160, 50)) as pilot:
        await _settle(app, pilot)
        focused = app.focused
        other = app.screen.query_one("#trending", Shelf)
        assert other is not focused, "test premise: two different shelves"
        other.reselect()
        await pilot.pause()

        def cursor(shelf):
            return next(c for c in shelf.cards if c.selected)

        # Measured as separation from the plate the cards sit on, not as alpha.
        # Alpha was standing in for "stronger" and stopped meaning that the
        # moment the unfocused cursor became an opaque colour a tier up.
        plate = focused.styles.background

        def against_plate(card):
            bg = card.styles.background
            solid = bg if bg.a == 1 else plate.blend(bg, bg.a)
            return (abs(solid.r - plate.r) + abs(solid.g - plate.g)
                    + abs(solid.b - plate.b))

        assert against_plate(cursor(focused)) > against_plate(cursor(other)), (
            "the focused shelf's cursor is no stronger than an unfocused one"
        )
        # Shape as well as colour, so the distinction survives a monochrome
        # terminal. The selected card draws an accent rule under its art, and
        # `-on` is the class the lift hangs on, so both have to be there.
        assert cursor(focused).has_class("-on")


async def test_motion_keys_move_along_a_shelf():
    """The first things a terminal user reaches for.

    Sideways now, not down: a shelf is horizontal, so `j`/`k` are what leave it
    for the shelf above or below (see `test_home_design`). Only `h` is bound
    inside a shelf, because `l` opens My List app-wide and one key cannot mean
    two things depending on where the cursor happens to be.
    """
    app = _app()
    async with app.run_test(size=(160, 50)) as pilot:
        await _settle(app, pilot)
        shelf = app.focused
        if len(shelf.cards) < 2:
            shelf = app.screen.query_one("#trending", Shelf)
            shelf.focus()
            await pilot.pause()
        n = len(shelf.cards)
        assert n >= 2, "fixture needs at least two cards to move between"

        await pilot.press("right")
        await pilot.pause()
        assert shelf.index == 1

        await pilot.press("h")
        await pilot.pause()
        assert shelf.index == 0

        await pilot.press("end")
        await pilot.pause()
        assert shelf.index == n - 1

        await pilot.press("home")
        await pilot.pause()
        assert shelf.index == 0


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

        chosen = app.screen.query_one("#trending", Shelf)
        chosen.focus()
        await pilot.pause()

        # Re-run the hook every late shelf calls once its cards are mounted.
        app.screen._adopt_focus()
        await pilot.pause()

        assert app.focused is chosen, "a late shelf stole the keyboard"
