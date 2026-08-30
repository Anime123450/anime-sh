"""The home screen is a grid, and a grid is only worth having if it stays
aligned. These protect the two properties that make it readable — every column
starts at the same cell on every row, and the row never exceeds the width it was
given — against the inputs most likely to break them: CJK titles, titles far
longer than the column, and terminals too narrow for the full layout.
"""

from __future__ import annotations

from rich.cells import cell_len

from anime_sh.tui.rows import (
    GAP,
    GLYPH_W,
    MEASURE_MAX,
    TITLE_MIN,
    Columns,
    Row,
    columns_for,
    columns_for_space,
    fit,
    render,
)


def render_row(title: str, cols: Columns) -> str:
    """One representative row, so the tests below vary only the title."""
    return render(Row(title=title, position="Ep 8/12", status="new episode"), cols)


def _plain(markup: str) -> str:
    """Strip the markup tags so we can measure what the terminal actually shows."""
    from rich.text import Text

    return Text.from_markup(markup).plain


def test_fit_pads_short_text_to_exactly_the_column_width():
    assert fit("abc", 8) == "abc     "
    assert cell_len(fit("abc", 8)) == 8


def test_fit_truncates_with_an_ellipsis_rather_than_overflowing():
    out = fit("Rich Girl Caretaker: I'm Secretly the Caregiver", 20)
    assert cell_len(out) == 20
    assert out.endswith("…")


def test_fit_measures_cjk_in_cells_not_characters():
    """"進撃の巨人" is 5 characters and 10 terminal cells. Padding by len() would
    overshoot by 5 cells and push every column after it out of alignment — on
    exactly the titles this app is most likely to be showing."""
    title = "進撃の巨人"
    assert len(title) == 5
    assert cell_len(title) == 10
    assert cell_len(fit(title, 20)) == 20
    # And truncation must not cut a wide character in half.
    assert cell_len(fit(title, 7)) == 7


def test_columns_are_identical_for_every_row_regardless_of_title_length():
    """The whole point: the episode column starts at the same cell whether the
    title is "BLACK TORCH" or ninety-five characters of light-novel subtitle."""
    cols = columns_for(120)
    short = _plain(render_row("BLACK TORCH", cols))
    long = _plain(render_row(
        "Rich Girl Caretaker: I'm Secretly the Caregiver of the Most Popular "
        "Girl in This Rich Kid School", cols))

    offset = GLYPH_W + GAP + cols.title + GAP
    assert short[offset:offset + 7] == long[offset:offset + 7] == "Ep 8/12"


def test_a_row_never_exceeds_the_width_it_was_given():
    for width in (60, 80, 100, 140, 200):
        cols = columns_for(width)
        line = _plain(render_row("A title of quite unremarkable length", cols))
        assert cell_len(line) <= cols.width
        assert cols.width <= max(width, TITLE_MIN + GLYPH_W + GAP)


def test_wide_terminals_cap_the_measure_instead_of_sprawling():
    """A line running the full width of a 200-column terminal makes the sweep
    back to the next title long enough to lose your place."""
    assert columns_for(200).width <= MEASURE_MAX
    assert columns_for(300).width == columns_for(200).width


def test_narrow_terminals_drop_the_least_load_bearing_column_first():
    """Status ("can I watch this now") outranks position ("which episode"),
    because a row you cannot act on is not worth reading the number of.

    Stated in cells of row space rather than terminal width. Which terminal
    width reaches which stage depends on `CHROME`, a fallback estimate that
    moves whenever the stylesheet does — and this test is about the order the
    columns are given up in, not about where the thresholds happen to land.
    """
    wide = columns_for_space(70)
    assert wide.position and wide.status

    tight = columns_for_space(40)
    assert tight.status and not tight.position

    tiny = columns_for_space(25)
    assert not tiny.position and not tiny.status
    assert tiny.title >= TITLE_MIN


def test_columns_are_only_ever_gained_as_the_terminal_grows():
    """The stronger property behind the case above: widening a terminal must
    never take a column away. Any threshold that is not monotonic produces a
    layout that flickers between two shapes as a window is dragged."""
    seen = [(w, columns_for(w)) for w in range(20, 220)]
    for (_, narrower), (width, wider) in zip(seen, seen[1:]):
        assert bool(wider.position) >= bool(narrower.position), width
        assert bool(wider.status) >= bool(narrower.status), width
        assert wider.width >= narrower.width, width


def test_a_dimmed_row_wraps_the_whole_line_not_just_the_title():
    line = render(
        Row(title="Slime Season 4", position="Ep 20", status="in 4d 0h", dim=True),
        columns_for(100),
    )
    assert line.startswith("[dim]") and line.endswith("[/dim]")


def test_a_bracketed_title_survives_rendering():
    """"[Oshi no Ko]" is markup to the renderer; unescaped, the row shows a
    blank where the title should be."""
    line = render_row("[Oshi no Ko]", columns_for(100))
    assert "[Oshi no Ko]" in _plain(line)


def test_the_year_badge_is_dropped_rather_than_pushing_the_grid_out_of_line():
    """The badge disambiguates two seasons with near-identical names, but the
    title is the more useful half — when both will not fit, the badge goes."""
    cols = columns_for(120)
    long_title = "From Old Country Bumpkin to Master Swordsman " * 2
    line = _plain(render(Row(title=long_title, badge="2025"), cols))
    assert "(2025)" not in line
    assert cell_len(line.rstrip()) <= cols.width
