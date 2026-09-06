"""The detail screen's episode layouts.

There is no arrangement that suits everyone. A twelve-part season reads best as
a block; a four-figure series wants the tightest grid it can get; and some people
would simply rather see one episode per line with its state spelled out, which is
what this screen looked like before the grid. So it is a setting, and these pin
what each option actually does.
"""

from __future__ import annotations

import pytest
from textual.widgets import ListView

import anime_sh.tui.screens.detail as detail_mod
from anime_sh.layout_names import EPISODE_LAYOUTS
from anime_sh.tui.widgets import EpisodeItem

from .test_app import _anime, _make_app


@pytest.fixture
def layout(monkeypatch):
    """Drive the screen's layout without touching the user's real config."""

    def _use(name: str):
        monkeypatch.setattr(
            detail_mod.DetailScreen, "_configured_layout", staticmethod(lambda: name)
        )

    return _use


async def test_the_grid_does_not_stretch_its_cells_across_the_window(layout):
    """The regression this whole thing started from.

    `grid_size_columns` sets how many columns there are, not how wide they are —
    a Textual grid shares its full width between them. Twelve columns on a
    190-column terminal came out fifteen cells wide holding five cells of
    content, so a twelve-episode season was strung across the whole window with
    the numbers floating fourteen spaces apart.

    The unit is what matters here, not just the number: a fractional column is
    exactly what stretches.
    """
    from textual.css.scalar import Unit

    layout("grid")
    app, _ = _make_app()
    d = detail_mod.DetailScreen(_anime(1, "A Show", eps=12))
    async with app.run_test(size=(190, 44)) as pilot:
        await pilot.pause()
        await app.push_screen(d)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        lv = d.query_one("#episodes", ListView)
        assert lv.styles.grid_columns, "columns are still free to stretch"
        scalar = lv.styles.grid_columns[0]
        assert scalar.unit is Unit.CELLS, f"column width is {scalar.unit}, not cells"
        assert int(scalar.value) == d._cell_width()


async def test_each_layout_arranges_the_episodes_differently(layout):
    """Three options that come out the same are one option with three names."""
    seen = {}
    for name in EPISODE_LAYOUTS:
        layout(name)
        app, _ = _make_app()
        d = detail_mod.DetailScreen(_anime(1, "A Show", eps=1175))
        async with app.run_test(size=(190, 44)) as pilot:
            await pilot.pause()
            await app.push_screen(d)
            await pilot.pause()
            d._numbers = [float(n) for n in range(1, 1176)]
            seen[name] = d._episode_columns()
    assert seen["list"] == 1, "the list layout is not one episode per line"
    assert seen["compact"] > seen["grid"], (
        f"compact is no tighter than grid ({seen})"
    )


async def test_compact_narrows_the_padding_with_the_cell(layout):
    """`_CELL_GAP` is not a gutter drawn between cells — it is the row padding
    from the stylesheet. Narrowing the column without narrowing the padding left
    the content three cells to render in, and every episode number was clipped to
    its first digit: `10`, `11`, `12` all displayed as `1`.
    """
    layout("compact")
    app, _ = _make_app()
    d = detail_mod.DetailScreen(_anime(1, "A Show", eps=120))
    async with app.run_test(size=(190, 44)) as pilot:
        await pilot.pause()
        await app.push_screen(d)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        lv = d.query_one("#episodes", ListView)
        assert lv.has_class("-compact")
        item = next(c for c in lv.children if isinstance(c, EpisodeItem))
        pad = item.styles.padding
        # glyph + space + the widest number + the on-disk slot.
        content = 3 + len(f"{max(d._numbers, default=1):g}")
        assert d._cell_width() - (pad.left + pad.right) >= content, (
            f"a {d._cell_width()}-cell column with {pad.left + pad.right} cells of "
            f"padding cannot show {content} cells of episode"
        )


async def test_the_list_layout_shows_each_episodes_state_in_its_row(layout):
    """The point of the list: the sentence that otherwise only appears for the
    episode under the cursor is on every row."""
    layout("list")
    app, _ = _make_app()
    d = detail_mod.DetailScreen(_anime(1, "A Show", eps=6))
    async with app.run_test(size=(190, 44)) as pilot:
        await pilot.pause()
        await app.push_screen(d)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        lv = d.query_one("#episodes", ListView)
        assert lv.styles.layout is not None
        item = next(c for c in lv.children if isinstance(c, EpisodeItem))
        assert item.layout_name == "list"
        assert "Episode" in item.detail


def test_an_unknown_layout_is_refused_where_it_is_typed():
    """Same reasoning as the theme name: silently falling back to the default
    makes a typo indistinguishable from the setting not working."""
    from anime_sh.config.schema import UiConfig

    assert UiConfig(episodes="compact").episodes == "compact"
    with pytest.raises(Exception, match="unknown episode layout"):
        UiConfig(episodes="mosaic")


def test_the_layout_names_and_the_screen_cannot_drift():
    """The picker cycles this tuple; the screen switches on these strings."""
    assert set(EPISODE_LAYOUTS) == {"grid", "list", "compact"}
