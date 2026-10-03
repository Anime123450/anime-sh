"""A search that matches nothing has to say so.

Searching hides the browse sections to make room for results, so a query AniList
could not match left the entire screen blank under a "Results" heading — no
indication of whether it was still loading, broken, or simply had no answer.
AniList's search is strict about word boundaries, so this is not a rare state.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input, Label, ListView

from anime_sh.tui import AnimeShApp, TuiServices

from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _noop


class EmptySearch(FakeSearch):
    async def search(self, query, *, limit=25):
        return []


def _app(search) -> AnimeShApp:
    services = TuiServices(search=search, metadata=FakeMetadata(),
                           library=FakeLibrary(), playback=FakePlayback(),
                           aclose=_noop)
    return AnimeShApp(services, theme="tokyo-night")


async def _type(pilot, app, text: str) -> None:
    box = app.screen.query_one("#search", Input)
    box.value = text
    box.post_message(Input.Changed(box, box.value))
    await pilot.pause()
    # The search is debounced by 0.3s; wait it out, then let the worker land.
    await asyncio.sleep(0.5)
    await app.workers.wait_for_complete()
    await pilot.pause()


async def _settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_a_search_with_no_matches_explains_itself():
    """The regression test: this used to be an empty screen."""
    app = _app(EmptySearch())
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _type(pilot, app, "zzzqqqxnotarealshow")

        notice = app.screen.query_one("#results-empty", Label)
        assert notice.display, "no explanation was shown for an empty result set"
        assert "zzzqqqxnotarealshow" in str(notice.render())


async def test_the_notice_does_not_outlive_the_search_that_caused_it():
    """Clearing the box brings the browse sections back; a stale "no matches"
    sitting under them would be worse than none."""
    app = _app(EmptySearch())
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _type(pilot, app, "nothing-matches-this")
        assert app.screen.query_one("#results-empty", Label).display

        await _type(pilot, app, "")

        assert not app.screen.query_one("#results-empty", Label).display
        assert app.screen.query_one("#sec-trending").display, "browse did not return"


async def test_a_search_that_matches_shows_no_notice():
    """The common path must not grow an explanation it does not need."""
    app = _app(FakeSearch())
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _type(pilot, app, "frieren")

        assert not app.screen.query_one("#results-empty", Label).display
        assert len(app.screen.query_one("#results", ListView).children) == 2


async def test_escape_clears_the_search():
    """The notice tells you to press escape, so escape has to work.

    It is bound app-wide to "go back", which on the base screen has nothing to
    pop and so did nothing — leaving no way out of a search but selecting the box
    and deleting it by hand. A hint that names a key that does nothing is worse
    than no hint.
    """
    app = _app(EmptySearch())
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await _type(pilot, app, "zzzqqqx")
        assert app.screen.query_one("#search", Input).value

        await pilot.press("escape")
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()

        assert app.screen.query_one("#search", Input).value == ""
        assert not app.screen.query_one("#results-empty", Label).display
        assert app.screen.query_one("#sec-trending").display


async def test_escape_on_an_empty_box_is_harmless():
    """Escape with nothing typed must not blow up or disturb the browse lists."""
    app = _app(FakeSearch())
    async with app.run_test() as pilot:
        await _settle(app, pilot)

        await pilot.press("escape")
        await pilot.pause()

        assert app.screen.query_one("#search", Input).value == ""
        assert app.screen.query_one("#sec-trending").display


async def test_searching_puts_the_shell_into_search_mode():
    """Searching hides all four shelves, so the furniture that describes them
    has to go with them.

    Each of these was wrong in the first version of the redesign, and none of
    them is visible from the code — the screenshot is what showed that the nav
    rail was still offering four destinations that did not exist, with four
    digits bound to hidden lists behind them, and that the hero was still
    carrying next week's broadcast schedule while you searched for something
    else.

    The rail is *emptied*, not hidden, and that distinction is the point: taking
    its column away with its contents shifted everything beside it five cells
    left — twenty at 160 — on the first character typed, so the result list
    landed somewhere the shelves had never been.
    """
    app = _app(FakeSearch())
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        nav = app.screen.query_one("#nav")
        assert nav.display, "test premise: the rail is showing at 120 columns"
        before = app.screen.query_one("#body").region.x

        await _type(pilot, app, "a")

        assert nav.display, "the rail's column went away and took the layout with it"
        assert not str(nav.render()).strip(), (
            "the nav rail still maps the hidden shelves"
        )
        assert app.screen.query_one("#body").region.x == before, (
            "the content column moved sideways when the search opened"
        )
        assert not app.screen.query_one("#sec-rail").display, (
            "the hero is still showing next week's schedule mid-search"
        )
        bar = str(app.screen.query_one("#actionbar").render())
        assert "back" in bar, f"no way out offered in the action bar: {bar!r}"
        assert "next shelf" not in bar, (
            "the action bar names a key that does nothing — there is one shelf"
        )


async def test_leaving_the_search_puts_the_shell_back():
    """And it has to come back, or the rail and schedule are gone for the rest
    of the session after one search."""
    app = _app(FakeSearch())
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        await _type(pilot, app, "a")
        await _type(pilot, app, "")

        assert app.screen.query_one("#nav").display, "the nav rail never came back"
        assert app.screen.query_one("#sec-rail").display, (
            "the schedule never came back"
        )


async def test_a_search_with_no_matches_leaves_no_empty_furniture():
    """A "Results" heading over an empty plate over a notice saying there are
    none is the same fact three times, and the heading and plate still cost
    their padding — two blank rows between the box and the explanation."""
    app = _app(EmptySearch())
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        await _type(pilot, app, "zzzqqqx")

        assert not app.screen.query_one("#sec-results").display, (
            "a Results heading is standing over nothing"
        )
        assert not app.screen.query_one("#results", ListView).display, (
            "an empty plate is still drawing its padding"
        )
        # The hero described whichever row the cursor sat on before the search —
        # a show that by definition is not among the results.
        hero = str(app.screen.query_one("#rail-preview").render())
        assert "Nothing to show" in hero, (
            f"the hero is still detailing a show the search did not find: {hero!r}"
        )
