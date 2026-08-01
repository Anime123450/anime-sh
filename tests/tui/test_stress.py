"""Abusive interaction: rapid keys, rapid screen changes, extreme terminal sizes.

Users mash keys and resize windows. None of it should raise.
"""

from __future__ import annotations

from textual.widgets import ListView

from .test_app import _make_app  # reuse the fake-service harness


async def test_rapid_typing_and_clearing_the_search_box():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(3):
            for ch in "frieren":
                await pilot.press(ch)
            for _ in range(len("frieren")):
                await pilot.press("backspace")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running


async def test_mashing_navigation_keys():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(4):
            for key in ("down", "down", "up", "enter", "escape", "l", "escape",
                        "question_mark", "escape", "/", "escape"):
                await pilot.press(key)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running


async def test_extreme_terminal_sizes():
    """A 20x5 terminal and a very large one must both render without raising."""
    app, _ = _make_app()
    async with app.run_test(size=(20, 5)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running
        for size in ((10, 3), (300, 100), (40, 10), (200, 60)):
            await pilot.resize_terminal(*size)
            await pilot.pause()
        assert app.is_running
        assert app.query_one("#continue", ListView) is not None


async def test_rapid_screen_switching_does_not_leave_the_app_wedged():
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(6):
            await pilot.press("l")       # My List
            await pilot.press("escape")
            await pilot.press("question_mark")  # Help
            await pilot.press("escape")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running
        assert app.screen is not None
