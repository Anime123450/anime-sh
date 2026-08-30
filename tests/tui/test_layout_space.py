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

from anime_sh.tui.rows import CHROME, Row, columns_for, title_cells, title_target
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
    _widest_row = HomeScreen._widest_row
    _body_width = HomeScreen._body_width
    _rail_showing = HomeScreen._rail_showing

    def __init__(self, width: int, rows: list[Row] | None = None) -> None:
        self.size = SimpleNamespace(width=width)
        self._rows = rows or []
        self._items: list = []

    def query(self, *_args):
        return self._items

    def resolve(self):
        """What the screen would actually lay out at this width."""
        cols = columns_for(self._body_width(), title_target(self._rows))
        self._items = [SimpleNamespace(_cols=cols)]
        rail = self._rail_width(self.size.width) if self._rail_showing() else 0
        return cols, rail, self.size.width - rail


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


def test_the_leftover_goes_to_the_rail_not_to_a_gutter():
    """Rows size themselves to their content and stop short of the body's edge.
    Those cells belong to the rail — left in the middle they read as two regions
    that failed to meet."""
    _cols, rail, _body = _Geometry(200, LIBRARY).resolve()
    base = _Geometry(200, LIBRARY)._rail_base(200)
    assert rail > base, "the rail did not take the width the rows left unused"


def test_the_gutter_is_a_margin_and_not_a_void():
    """54 empty columns was the complaint. The rail is capped, so *some* residue
    is expected on a very wide terminal — it just has to read as a margin."""
    cols, rail, body = _Geometry(200, LIBRARY).resolve()
    gutter = body - CHROME - cols.width
    assert gutter <= 24, f"{gutter} columns left sitting between the rows and the rail"


def test_the_rail_never_starves_the_rows():
    """The rail grows into spare width, so it must never take width that is not
    actually spare."""
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
    g = _Geometry(400, LIBRARY)
    _cols, rail, _body = g.resolve()
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
