"""A thousand-episode show must not freeze the screen while it mounts.

Opening ONE PIECE built 1175 episode cells and mounted them in one go: nothing
on screen for 3.8 seconds, and the app unanswerable for all of it. Building the
cells is nearly free — 0.09s — so the cost is entirely Textual mounting them,
which means the only lever is mounting fewer at a time.

They are mounted in chunks now, yielding to the event loop between them: first
cells up in 0.3s, and the screen answers keys while the rest fill in. Measured
before and after with the same harness.

Timing is not asserted here — it is the reason for the change, not a property a
test should race on. What is asserted is the mechanism that produces it.
"""

from __future__ import annotations

from textual.widgets import ListView

from anime_sh.tui.screens.detail import DetailScreen

from .test_app import _anime, _make_app


def _count_extends(detail, monkeypatch):
    """Record how many separate mount batches the grid is given."""
    grid = detail.query_one("#episodes", ListView)
    calls: list[int] = []
    real = grid.extend

    def spy(items):
        items = list(items)
        calls.append(len(items))
        return real(items)

    monkeypatch.setattr(grid, "extend", spy)
    return calls


async def test_a_long_show_is_mounted_in_chunks(monkeypatch):
    """One blocking batch is what froze the screen. Many small ones is the fix,
    and the cells all still arrive."""
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = DetailScreen(_anime(1, "ONE PIECE", eps=1175))
        await app.push_screen(detail)
        await pilot.pause()
        calls = _count_extends(detail, monkeypatch)

        await detail._render_episodes([float(n) for n in range(1, 1176)])
        await pilot.pause()

        assert len(calls) > 1, "mounted in one blocking batch again"
        assert max(calls) <= DetailScreen._RENDER_CHUNK
        assert sum(calls) == 1175, "episodes went missing in the chunking"
        assert len(detail.query_one("#episodes", ListView).children) == 1175


async def test_a_normal_length_show_is_still_one_batch(monkeypatch):
    """The chunking must not make the common case slower or stranger. Twelve
    episodes is one batch, exactly as before."""
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = DetailScreen(_anime(1, "Frieren", eps=12))
        await app.push_screen(detail)
        await pilot.pause()
        calls = _count_extends(detail, monkeypatch)

        await detail._render_episodes([float(n) for n in range(1, 13)])
        await pilot.pause()

        assert calls == [12]


async def test_the_cursor_still_lands_on_the_next_episode_far_into_a_long_show():
    """The cursor is placed as soon as its own cell exists rather than after the
    whole grid, so the episode you came to play is selected early. That placement
    has to be *correct* — off-by-a-chunk would park you in the wrong decade of
    ONE PIECE."""
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = DetailScreen(_anime(1, "ONE PIECE", eps=1175),
                              resume_episode=1090.0)
        await app.push_screen(detail)
        await pilot.pause()

        await detail._render_episodes([float(n) for n in range(1, 1176)])
        await pilot.pause()

        grid = detail.query_one("#episodes", ListView)
        assert grid.index == 1089, "cursor is not on episode 1090"


async def test_a_render_stops_as_soon_as_a_newer_one_arrives(monkeypatch):
    """`_populate_episodes_worker` renders twice — once from AniList's planned
    count, then again from what the provider actually has. Without this the
    second call queued behind the first, so a long show mounted a thousand cells
    only to clear them and mount them again."""
    app, _ = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = DetailScreen(_anime(1, "ONE PIECE", eps=1175))
        await app.push_screen(detail)
        await pilot.pause()

        grid = detail.query_one("#episodes", ListView)
        calls: list[int] = []
        real = grid.extend

        def spy(items):
            items = list(items)
            calls.append(len(items))
            # Stand in for a newer render arriving while this one is mid-flight.
            detail._render_gen += 1
            return real(items)

        monkeypatch.setattr(grid, "extend", spy)
        await detail._render_episodes([float(n) for n in range(1, 1176)])
        await pilot.pause()

        assert len(calls) == 1, "kept mounting after being superseded"
