"""Column geometry for the home-screen lists — pure, testable, no widgets.

Rows used to be built by gluing a title to a subtitle with two spaces, so where
the metadata began depended entirely on how long the title was. With titles
running from "BLACK TORCH" to "Rich Girl Caretaker: I'm Secretly the Caregiver
of the Most Popular Girl in This Rich Kid School", the answer to "which episode
am I on" landed in a different column on every line, and finding it meant
reading each row to its end.

So the row is a grid instead. The eye travels down one column, not across forty
lines:

    ▸  The King's Avatar          Ep 2      ━━━━━━───  68%
    ●  The Ogre's Bride           Ep 8/12   new episode
    ○  Slime Season 4             Ep 20     in 4d 0h

Widths come from the terminal, not from constants, because the same list has to
work in an 80-column shell and a 200-column one.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len, set_cell_size

# Terminal cells, not characters. A CJK title ("進撃の巨人") is 5 characters and
# 10 cells wide, so padding by len() would push every column after it out of
# alignment on exactly the titles most likely to appear here.
GAP = 2
GLYPH_W = 1
POSITION_W = 9  # "Ep 12/24"
STATUS_W = 16  # "new episode", "in 4d 0h", a bar plus its percentage

# A list is read by scanning down it, and a line that runs the full width of a
# 200-column terminal makes the return sweep to the next title long enough to
# lose your place. Cap the measure and leave the right margin empty.
MEASURE_MAX = 96
TITLE_MIN = 18

# Chrome between the screen edge and a row's own text: #body padding (2 each
# side), ListItem padding (1 each side), and the scrollbar the body reserves.
CHROME = 4 + 2 + 2


@dataclass(frozen=True, slots=True)
class Columns:
    """Resolved widths for one list. ``position``/``status`` of 0 mean the
    column is dropped, which is how the grid degrades in a narrow terminal
    rather than wrapping into an unreadable mess."""

    title: int
    position: int
    status: int

    @property
    def width(self) -> int:
        w = GLYPH_W + GAP + self.title
        if self.position:
            w += GAP + self.position
        if self.status:
            w += GAP + self.status
        return w


def columns_for(screen_width: int) -> Columns:
    """Resolve column widths for a terminal ``screen_width`` cells wide.

    Drops the least load-bearing column first. Status ("can I watch this now")
    outranks position ("which episode"), because a row you cannot act on is not
    worth reading the episode number of.
    """
    content = min(max(screen_width - CHROME, TITLE_MIN + GLYPH_W + GAP), MEASURE_MAX)
    fixed = GLYPH_W + GAP

    title = content - fixed - (GAP + POSITION_W) - (GAP + STATUS_W)
    if title >= TITLE_MIN:
        return Columns(title=title, position=POSITION_W, status=STATUS_W)

    # Too tight for both: keep status, drop the episode position.
    title = content - fixed - (GAP + STATUS_W)
    if title >= TITLE_MIN:
        return Columns(title=title, position=0, status=STATUS_W)

    return Columns(title=max(content - fixed, TITLE_MIN), position=0, status=0)


def _lit(text: str) -> str:
    """Escape markup so a title renders literally.

    Titles routinely carry square brackets — "[Oshi no Ko]", a "[Mini]" batch —
    which Textual's markup parser would otherwise swallow as a style tag, making
    the text disappear entirely.
    """
    return text.replace("\\", "\\\\").replace("[", r"\[")


def fit(text: str, width: int) -> str:
    """``text`` in exactly ``width`` terminal cells: padded, or truncated with an
    ellipsis. Measured in cells so CJK titles align like any other."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text + " " * (width - cell_len(text))
    if width == 1:
        return "…"
    # set_cell_size handles the case where cutting would land mid-wide-character.
    return set_cell_size(text, width - 1) + "…"


@dataclass(frozen=True, slots=True)
class Row:
    """One list row, in semantic pieces rather than a pre-glued string."""

    title: str
    glyph: str = " "  # a single cell, already wrapped in its colour
    position: str = ""
    status: str = ""  # may carry markup (a progress bar); see status_cells
    status_cells: int | None = None  # visible width when status carries markup
    badge: str = ""  # disambiguating year for near-identical season titles
    dim: bool = False
    # Sort rank, low first. Continue Watching is ordered by how ready the row is
    # to be acted on, not by when it was last touched: the episode you are
    # halfway through is the likeliest thing you came here to do, so it leads.
    rank: int = 1


def render(row: Row, cols: Columns) -> str:
    """Compose one row into a Textual markup string of exactly ``cols.width``.

    Every column is padded to its own width even when empty, because a grid that
    collapses on short content is not a grid — the next row's title would start
    somewhere else and the alignment would be lost precisely where the list gets
    interesting.
    """
    title = _lit(row.title)
    plain_width = cell_len(row.title)

    if row.badge:
        # Parenthesised, and dimmer than the title: a bare year tacked on reads
        # as part of the name ("…Master Swordsman 2025"), which defeats the point
        # of adding it to tell two seasons apart.
        badge = f" ({row.badge})"
        if plain_width + cell_len(badge) <= cols.title:
            title = f"{title}[dim] ({_lit(row.badge)})[/dim]"
            plain_width += cell_len(badge)
        # No room for the badge: the title itself is the more useful information.

    if plain_width < cols.title:
        title += " " * (cols.title - plain_width)
    elif plain_width > cols.title:
        title = _lit(fit(row.title, cols.title))

    cells = [row.glyph, " " * GAP, title]

    if cols.position:
        cells += [" " * GAP, fit(row.position, cols.position)]

    if cols.status:
        visible = row.status_cells if row.status_cells is not None else cell_len(row.status)
        if visible <= cols.status:
            status = row.status + " " * (cols.status - visible)
        else:
            # Markup can't be blindly truncated, so a caller that oversizes a
            # marked-up status gets it left intact rather than corrupted.
            status = row.status if row.status_cells is not None else fit(row.status, cols.status)
        cells += [" " * GAP, status]

    line = "".join(cells).rstrip()
    return f"[dim]{line}[/dim]" if row.dim else line
