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
