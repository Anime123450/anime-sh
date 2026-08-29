"""Episodes are a grid, and the cursor moves like one.

The episode list rendered one episode per line: 28 stacked rows for Frieren on a
150-column terminal, and 1175 for ONE PIECE. A list of numbers is the one thing
that genuinely wants to tile, and tiling it is the difference between three rows
and a thousand.

Tiling forced two consequences worth pinning down. A grid cell has to be a fixed
width, so the sentence that used to sit in the row — "· not on this source", the
countdown, the progress bar — moved to a detail line under the grid. And a
ListView cursor is linear, so Down went to the next *item*, which in a grid reads
as one cell to the right.
"""

from __future__ import annotations

import contextlib

from textual.app import App, ComposeResult

from anime_sh.tui.widgets import EpisodeGrid, EpisodeItem


class _GridApp(App):
    """A ListView's `index` is a reactive and its children are not real until it
    is mounted, so an unmounted grid reports `index is None` and no children.
    These tests drive a real one."""

    def __init__(self, count: int, columns: int) -> None:
        super().__init__()
        self._count = count
        self._columns = columns

    def compose(self) -> ComposeResult:
        grid = EpisodeGrid(*[EpisodeItem(float(n)) for n in range(1, self._count + 1)],
                           id="episodes")
        grid.columns = self._columns
        yield grid


@contextlib.asynccontextmanager
async def _grid(count: int, columns: int):
    app = _GridApp(count, columns)
    async with app.run_test() as pilot:
        await pilot.pause()
        yield app.query_one("#episodes", EpisodeGrid), pilot


def test_a_cell_carries_only_state_and_number():
    """Uniform width is what makes tiling possible at all."""
    cell = str(EpisodeItem(5.0)._cell(5.0, False, 0, True, None, False, 4))
    assert "Episode" not in cell
    assert "5" in cell


def test_numbers_are_right_aligned_to_a_common_width():
    """9 and 1175 have to sit in the same grid without ragging the columns."""
    narrow = EpisodeItem._cell(9.0, False, 0, True, None, False, 4)
    wide = EpisodeItem._cell(1175.0, False, 0, True, None, False, 4)
    assert len(_plain(narrow)) == len(_plain(wide))


def _plain(markup: str) -> str:
    import re

    return re.sub(r"\[/?[^\]]+\]", "", markup)


def test_the_sentence_survives_on_the_item_even_though_the_cell_drops_it():
    """"not on this source" and "hasn't aired yet" are different facts, and the
    screen still has to be able to say which. The detail line is where they go —
    losing them to make room for a grid would be a bad trade."""
    unavailable = EpisodeItem(7.0, available=False, air_label="airs in 2d")
    assert "airs in 2d" in unavailable.detail
    assert "Episode 7" in unavailable.detail

    missing = EpisodeItem(8.0, available=False)
    assert "not on this source" in missing.detail


async def test_down_moves_a_whole_row():
    """The regression test. This was first written as a *screen* binding, which
    silently did nothing: the focused widget's bindings are consulted before the
    screen's, so Down kept reaching ListView's own linear cursor and moved the
    selection by one. It has to live on the list itself.
    """
    async with _grid(28, columns=12) as (grid, pilot):
        grid.index = 0
        await pilot.pause()

        grid.action_cursor_down()
        assert grid.index == 12

        grid.action_cursor_up()
        assert grid.index == 0


async def test_the_cursor_clamps_rather_than_wrapping():
    """Wrapping in a grid throws the cursor corner to corner instead of moving it
    a step — disorienting in a way it never is in a flat list."""
    async with _grid(28, columns=12) as (grid, pilot):
        grid.index = 0
        await pilot.pause()
        grid.action_cursor_up()
        assert grid.index == 0, "moved above the first episode"

        grid.index = 27
        grid.action_cursor_down()
        assert grid.index == 27, "moved past the last episode"


async def test_a_partial_last_row_does_not_overshoot():
    """28 episodes in rows of 12 leaves a row of four. Stepping down from the
    middle of the second row must land on the last episode, not off the end."""
    async with _grid(28, columns=12) as (grid, pilot):
        grid.index = 20  # second row, ninth column — no episode beneath it
        await pilot.pause()

        grid.action_cursor_down()
        assert grid.index == 27


async def test_a_single_column_grid_behaves_like_an_ordinary_list():
    """`columns` defaults to 1, so anything that has not been sized yet still
    moves one row at a time rather than not at all."""
    async with _grid(5, columns=1) as (grid, pilot):
        grid.index = 0
        await pilot.pause()
        grid.action_cursor_down()
        assert grid.index == 1


def test_a_downloaded_episode_is_marked_without_ragging_the_grid():
    """"On disk" is a second, independent fact: an episode can be watched *and*
    downloaded, or unwatched and downloaded, so it cannot share the glyph that
    already carries watch state. It gets a trailing slot — a space when absent,
    so every cell stays the same width and the columns stay columns.
    """
    plain = _plain(EpisodeItem._cell(1.0, False, 0, True, None, False, 4, False))
    on_disk = _plain(EpisodeItem._cell(1.0, False, 0, True, None, False, 4, True))

    assert len(plain) == len(on_disk), "the marker changed the cell width"
    assert on_disk != plain, "a downloaded episode looks identical to one that isn't"


def test_every_episode_state_can_also_be_marked_on_disk():
    """Watched, in-progress, up-next and unavailable are all states an episode
    can be in while still sitting on disk. A marker that only works for one of
    them would be worse than none, because its absence would mean nothing."""
    # `_cell` directly, not `item.children[0]`: a ListItem's children are not
    # real until it is mounted, and reaching for them here raises IndexError.
    # (watched, resume_s, available, progress_pct, is_next)
    states = {
        "watched": (True, 0, True, None, False),
        "in progress": (False, 0, True, 40, False),
        "up next": (False, 0, True, None, True),
        "unavailable": (False, 0, False, None, False),
        "plain": (False, 0, True, None, False),
    }
    for name, args in states.items():
        bare = _plain(EpisodeItem._cell(3.0, *args, 4, False))
        marked = _plain(EpisodeItem._cell(3.0, *args, 4, True))
        assert marked != bare, f"no on-disk marker for {name}"
        assert len(marked) == len(bare), f"width changed for {name}"


def test_the_detail_line_says_it_in_words():
    """The marker is a glyph in a 5-cell box; the line underneath is where it
    gets said in a way that needs no legend."""
    assert "on disk" in EpisodeItem(1.0, downloaded=True).detail
    assert "on disk" not in EpisodeItem(1.0).detail
