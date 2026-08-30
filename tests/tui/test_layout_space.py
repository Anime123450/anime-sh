"""The window has to be spent, not left in a gutter.

On a 200-column terminal the row grid stopped at 96 cells and the rail was a
fixed 42, leaving 54 empty columns between them — while *both* regions were
ellipsizing titles. Space sitting unused between two things that are truncating
is the layout failing at the one job it has.

These tests pin the arithmetic that fixes it. They are about geometry, not
appearance, so they are all pure width maths driven through the real methods.
"""

from __future__ import annotations

from types import SimpleNamespace

from anime_sh.tui.rows import (
    CHROME,
    Row,
    columns_for,
    columns_for_space,
    title_cells,
    title_target,
)
from anime_sh.tui.screens.home import HomeScreen


class _Geometry:
    """HomeScreen's width arithmetic, without a mounted Textual app.

    Only the sizing methods are borrowed. Mounting the real screen would drag in
    workers, a database and the network, none of which have anything to do with
    how wide a column is.
    """

    _RAIL_MIN_WIDTH = HomeScreen._RAIL_MIN_WIDTH
    _RAIL_MIN_RAIL = HomeScreen._RAIL_MIN_RAIL
    _RAIL_MAX_RAIL = HomeScreen._RAIL_MAX_RAIL
    _RAIL_SHARE = HomeScreen._RAIL_SHARE
    _rail_base = HomeScreen._rail_base
    _rail_width = HomeScreen._rail_width
    _body_width = HomeScreen._body_width
    _rail_showing = HomeScreen._rail_showing

    def __init__(self, width: int, rows: list[Row] | None = None) -> None:
        self.size = SimpleNamespace(width=width)
        self._rows = rows or []
        self._items: list = []

    def query(self, *_args):
        return self._items

    def resolve(self):
        """What the screen would actually lay out at this width.

        Uses the `CHROME` estimate, because nothing is mounted here to measure.
        The real screen measures a mounted row instead — see
        `test_a_row_fits_inside_the_widget_that_holds_it`, which is the only
        test here that can catch the estimate drifting from the stylesheet.
        """
        rail = self._rail_width(self.size.width) if self._rail_showing() else 0
        body = self.size.width - rail
        cols = columns_for_space(body - CHROME, title_target(self._rows))
        self._items = [SimpleNamespace(_cols=cols)]
        return cols, rail, body


def _rows(*widths: int) -> list[Row]:
    return [Row(title="x" * w) for w in widths]


LIBRARY = _rows(11, 16, 16, 16, 17, 17, 18, 21, 31, 34, 41, 47, 48, 48, 52, 60, 68, 76, 80, 96)


def test_the_rows_always_fit_the_column_holding_them():
    """The regression that started this. `columns_for` was handed the *terminal*
    width while the rows lived inside a body the rail had already shortened, so
    at 120 columns a 96-cell row was being laid out in a 78-cell body. Nothing
    reported it; the row was simply wider than its container.
    """
    for width in range(80, 261):
        cols, _rail, body = _Geometry(width, LIBRARY).resolve()
        assert cols.width <= body - CHROME, f"row overflows its body at {width} columns"


def test_the_rail_width_depends_only_on_the_terminal():
    """The rail used to absorb whatever Region B left unused, which looked
    better but could not survive rows being *measured* rather than computed: a
    wider rail makes a narrower body, which makes a narrower measured row, which
    leaves more spare, which widens the rail again. Its width has to be a
    function of something that does not depend on it."""
    g = _Geometry(200, LIBRARY)
    lean = _Geometry(200, _rows(11, 12, 13))
    assert g._rail_width(200) == lean._rail_width(200)


def test_the_gutter_is_a_margin_and_not_a_void():
    """54 empty columns was the complaint, and closing it no longer needs the
    rail to absorb anything: with one grid shared across the sections and a
    measure cap that a wide terminal can actually reach, the rows spend the
    width themselves."""
    cols, _rail, body = _Geometry(200, LIBRARY).resolve()
    gutter = body - CHROME - cols.width
    assert gutter <= 24, f"{gutter} columns left sitting between the rows and the rail"


def test_the_rail_never_starves_the_rows():
    """Region C may never take room Region B needs."""
    for width in (120, 140, 160, 180, 200, 240):
        cols, _rail, body = _Geometry(width, LIBRARY).resolve()
        assert body - CHROME >= cols.width, f"rail ate the rows at {width}"


def test_the_rail_keeps_growing_past_160_columns():
    """It used to stop at 42 cells forever, which is why every rail title was
    ellipsized at 27 characters on a 200-column terminal."""
    g = _Geometry(200)
    assert g._rail_base(200) > g._rail_base(160) > g._rail_base(120)


def test_the_rail_is_capped_so_it_stays_a_margin_note():
    """It is glanced at, not read. Past a point it stops supporting the list and
    starts competing with it."""
    _cols, rail, _body = _Geometry(400, LIBRARY).resolve()
    assert rail <= HomeScreen._RAIL_MAX_RAIL


def test_no_rail_at_all_below_the_breakpoint():
    """A narrow terminal is exactly as it was: rows only, full width."""
    g = _Geometry(100, LIBRARY)
    cols, rail, body = g.resolve()
    assert rail == 0 and body == 100
    assert cols.width <= body - CHROME


def test_the_title_column_follows_the_bulk_not_the_outlier():
    """One 96-cell title would otherwise push the episode column 50 cells right
    of where a short one ends, reintroducing the ragged gap the grid exists to
    close."""
    target = title_target(LIBRARY)
    widest = max(title_cells(r) for r in LIBRARY)
    assert target < widest, "the column is still being set by its longest title"
    fits = sum(1 for r in LIBRARY if title_cells(r) <= target)
    assert fits >= 0.85 * len(LIBRARY), "the column no longer fits most titles"


def test_a_wider_terminal_buys_a_wider_title_column():
    """The point of the whole change: width that exists should reach the text."""
    narrow, _r1, _b1 = _Geometry(140, LIBRARY).resolve()
    wide, _r2, _b2 = _Geometry(200, LIBRARY).resolve()
    assert wide.title > narrow.title


def test_a_list_of_short_titles_does_not_get_a_wide_column():
    """Growing into spare width must stay content-driven, or "BLACK TORCH" ends
    up with sixty spaces before its episode number."""
    short, _r, _b = _Geometry(200, _rows(11, 12, 14, 16)).resolve()
    assert short.title <= 20


def test_a_wide_terminal_reaches_the_column_the_content_asked_for():
    """The measure cap was 96 cells, which on a 200-column terminal held the
    title column at 64 even though the content wanted 68 and the width was
    sitting right there unused. A cap is meant to stop a line running the whole
    window, not to ellipsize titles a wide terminal could print in full.
    """
    target = title_target(LIBRARY)
    cols, _rail, _body = _Geometry(200, LIBRARY).resolve()
    assert cols.title == target, (
        f"title column stopped at {cols.title} while the content asked for {target}"
    )


def test_crossing_the_rail_breakpoint_relayouts_before_it_rebuilds():
    """Resizing past 120 columns changes the body width for *every* list, not
    just Continue Watching. An early return here left favourites, seasonal and
    trending laid out for the old width until something else happened to touch
    them."""
    import inspect

    src = inspect.getsource(HomeScreen.on_resize)
    assert src.index("self._apply_grid()") < src.index("self._load_continue()"), (
        "the rebuild short-circuits the relayout of the other lists"
    )


async def test_a_row_fits_inside_the_widget_that_holds_it():
    """`CHROME` is a hand-maintained sum of the paddings declared in `app.tcss`,
    and every other test in this file measures against it — so all of them stay
    green when it drifts from what the stylesheet actually does.

    This one asks the mounted widget instead. When the row plate gained
    horizontal padding, rows stayed two cells too wide and the last column was
    clipped: "new episode" rendered as "new" on a 100-column terminal, with 527
    tests passing.

    The list must be long enough to *scroll*. Two cells of `CHROME` are the
    scrollbar, and a short list does not have one — the first version of this
    test mounted a single row, found two cells of slack that only existed
    because nothing was overflowing, and passed against the very bug it was
    written for.
    """
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.widgets import Label, ListView

    from anime_sh.tui.widgets import AnimeItem

    class _Row:
        pass

    class _Probe(App):
        CSS_PATH = "../../src/anime_sh/tui/app.tcss"

        def __init__(self, cols) -> None:
            super().__init__()
            self.cols = cols

        def compose(self) -> ComposeResult:
            with VerticalScroll(id="body"):
                yield ListView(
                    *[
                        AnimeItem(
                            _Row(),
                            Row(title="x" * 40, position="Ep 9/12",
                                status="new episode"),
                            self.cols,
                        )
                        for _ in range(60)
                    ],
                    id="continue",
                )

    for width in (100, 120, 160, 200):
        cols = columns_for(width, title_target(LIBRARY))
        app = _Probe(cols)
        async with app.run_test(size=(width, 24)) as pilot:
            await pilot.pause()
            label = app.query_one(AnimeItem).query_one(Label)
            assert label.content_region.width >= cols.width, (
                f"at {width} columns a {cols.width}-cell row is drawn into "
                f"{label.content_region.width} cells — the last column is clipped"
            )
