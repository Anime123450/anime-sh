"""The application shell — the frame every screen sits inside.

Three pieces of furniture that never move, so spatial memory does the
navigating: a top bar that says where you are, a nav rail that says what else
there is, and an action bar that says what the thing under the cursor can do.

None of them hold state of their own. The rail is told which section has focus
and the action bar is told what is focused; both are pure renders of that. That
is deliberate — navigation lives in one place (the screen), and the furniture
reflects it rather than competing to own it.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.widgets import Static

from .rows import fit

#: The wordmark. Two half-blocks and the name — enough to be a logo at one row
#: tall, which is all a terminal can spare for identity.
WORDMARK = "▞▘"


def spaced(label: str) -> str:
    """``"CONTINUE"`` → ``"C O N T I N U E"``.

    Letterspacing is what lets a shelf label read as structure without a rule
    under it. The previous design drew `Continue Watching ───────────── 18`,
    spending forty cells of line art to carry one integer; the eye reads the
    spaced caps as a heading on their own, because nothing else on the screen is
    set that way.
    """
    return " ".join(label.upper())


@dataclass(frozen=True, slots=True)
class Section:
    """One destination: a shelf on the home screen and a row on the nav rail."""

    key: str  #: the digit that jumps to it
    icon: str  #: a single cell, legible without colour
    label: str  #: as the rail writes it
    list_id: str  #: the ListView it governs
    head_id: str  #: that list's heading Label
    #: Share of the screen this shelf gets relative to the others. Hierarchy has
    #: to be visible in *size*, not only in order: four shelves of equal height
    #: read as four equal things, and then the screen is a dashboard again. What
    #: you are in the middle of gets twice what you might browse.
    weight: int = 1


#: Ordered by what you most likely came for. Continue Watching is the reason the
#: app exists; Trending is where you land when the library is empty.
SECTIONS: tuple[Section, ...] = (
    Section("1", "▸", "Continue", "#continue", "#sec-continue", weight=3),
    Section("2", "♥", "Favourites", "#favorites", "#sec-favorites"),
    Section("3", "◷", "This Season", "#seasonal", "#sec-seasonal"),
    Section("4", "✦", "Trending", "#trending", "#sec-trending"),
)

#: Width bands, from the monospace-design standard. Named rather than inlined
#: because three widgets have to agree about which band they are in.
COMPACT, STANDARD, EXPANDED, WIDE = "compact", "standard", "expanded", "wide"


def band(width: int) -> str:
    if width < 80:
        return COMPACT
    if width < 120:
        return STANDARD
    if width < 160:
        return EXPANDED
    return WIDE


def nav_width(width: int) -> int:
    """Cells the nav rail takes, or 0 when it is not shown.

    Icons only in the standard band: at 80–119 cells every column spent on a
    label is one the titles do not get, and the icons carry the order on their
    own once you have seen the labels once.
    """
    b = band(width)
    if b == COMPACT:
        return 0
    # Icons until the terminal is genuinely wide. Labels were tried at 120 and
    # measured: the rail took 16 cells, the hero 40, and the title column fell
    # from 34 cells to 18 -- every row ellipsized to make room for four words
    # that do not change. Orientation is worth five cells, not sixteen.
    # Twenty, not eighteen: the rail spends eight cells on furniture and the
    # count, and at eighteen "This Season" came out as "This Seas…" — a nav rail
    # that cannot write its own destinations is worse than no labels at all.
    return 5 if b in (STANDARD, EXPANDED) else 20


class TopBar(Static):
    """Identity, where you are, and how to search — one row.

    Replaces Textual's `Header`, which spent a row on a centred app title and a
    clock. The clock is the terminal's job; the row is better spent saying which
    section is open, which is the one thing the old screen never said.
    """

    def render_bar(self, section: str, width: int, *, hint: str = "") -> None:
        if width <= 0:
            return
        left = f"[b $primary]{WORDMARK}[/] [b]anime-sh[/b]"
        left_cells = len(WORDMARK) + 1 + len("anime-sh")
        if band(width) == COMPACT:
            # No room for both identity and location; location wins, because the
            # name of the app is on the terminal's title bar anyway.
            self.update(f" [b]{fit(section, max(0, width - 2))}[/b]")
            return
        # What goes on the right, and the cells it needs. Plain text, so its
        # length is its width.
        tail = hint or "/ search   ? help"
        # The section is cut to what is left over, rather than the row being
        # allowed to overrun. The detail screen puts a show's whole title here
        # beside a "12/12 episodes available" hint, which at 80 columns came to
        # 84 cells and lost the end of the hint off the right edge — and the hint
        # is the half of that pair a reader cannot guess back.
        room = width - left_cells - len(tail) - 8
        if room < 8:
            # Not even both at their shortest. Location wins: it is the part
            # that changes, and `? help` is on every other screen anyway.
            tail = ""
            room = width - left_cells - 6
        section = fit(section, max(0, room)).rstrip()
        mid = f"[dim]·[/dim]  [$accent]{section}[/]"
        mid_cells = 4 + len(section)
        right = f"[dim]{tail}[/dim]" if tail else ""
        right_cells = len(tail)
        pad = max(1, width - left_cells - mid_cells - right_cells - 4)
        self.update(f" {left}  {mid}{' ' * pad}{right} ")


class NavRail(Static):
    """The sections, always in the same order, with the focused one marked.

    A map rather than a control. Tab already moves between shelves, so a rail
    that *also* took focus would be a second way to do one thing and a second
    place for the cursor to get lost in. It shows where Tab has got to, and the
    digit beside each row jumps straight there.
    """

    def render_nav(self, current: str | None, width: int, counts: dict,
                   *, blank: bool = False) -> None:
        if width <= 0:
            return
        if blank:
            # The column without its contents. Searching hides the shelves this
            # maps, but taking the column away with them moved everything beside
            # it — see the note at the call site.
            self.update("")
            return
        labelled = width >= 16
        lines: list[str] = [""]
        for s in SECTIONS:
            here = s.list_id == current
            count = counts.get(s.list_id, 0)
            if not labelled:
                # The digit, not just the icon. A column of four unexplained
                # glyphs is decoration; `1 ▸` is the key that goes there, which
                # is the only reason the rail is worth five cells at this width.
                if here:
                    lines.append(f"[$accent]▏[/][b]{s.key} {s.icon}[/b]")
                elif count:
                    lines.append(f" [dim]{s.key}[/dim] {s.icon}")
                else:
                    lines.append(f" [dim]{s.key} {s.icon}[/dim]")
                continue
            # 4 cells of furniture (marker, icon, two spaces) and 4 for the
            # count and the gap before it. Without that gap "Continue" and "18"
            # ran together into "Continue18".
            body = fit(s.label, max(4, width - 8))
            n = f"{count}" if count else ""
            # The marker replaces a cell rather than adding one, exactly as the
            # focused row's border does, so both lines are the same width and
            # neither wraps. Adding it pushed the focused line one cell over and
            # the count wrapped onto a line of its own.
            if here:
                lines.append(f"[$accent]▏[/][b] {s.icon} {body}[/b] [dim]{n:>2}[/dim]")
            elif count:
                lines.append(f" [dim]{s.icon}[/dim] {body} [dim]{n:>2}[/dim]")
            else:
                lines.append(f" [dim]{s.icon} {body}   [/dim]")
        if labelled:
            lines.append("")
            lines.append(f"  [dim]{fit('1-4  jump', width - 4)}[/dim]")
            lines.append(f"  [dim]{fit('z    expand', width - 4)}[/dim]")
        self.update("\n".join(lines))


class ActionBar(Static):
    """What the thing under the cursor can do, not what the app can do.

    The previous footer listed six global commands on every screen, in the same
    order, whatever was selected — so the one row guaranteed to be visible said
    nothing about the row you were looking at. Keys that only make sense for a
    selection appear only when there is one.
    """

    #: Always available, in the order they are most often wanted.
    _GLOBAL = (("/", "search"), ("?", "help"), ("q", "quit"))

    def render_actions(self, width: int, *, contextual=(), zoomed: bool = False,
                       zoom: bool = True) -> None:
        if width <= 0:
            return
        parts = list(contextual)
        # `zoom=False` for screens that do not bind `z`. A bar advertising a key
        # the screen ignores is worse than a shorter bar: the one row guaranteed
        # to be visible is the row a reader trusts.
        if zoom:
            parts.append(("z", "collapse" if zoomed else "expand"))
        parts.extend(self._GLOBAL)
        out, used = [], 0
        for key, label in parts:
            cells = len(key) + 1 + len(label) + 3
            if used + cells > width - 2:
                break
            out.append(f"[$accent]{key}[/] [dim]{label}[/dim]")
            used += cells
        self.update("  " + "   ".join(out))


def empty_state(title: str, hint: str, width: int) -> Text:
    """What a section says when it has nothing in it.

    A blank plate reads as "still loading" or as "broken"; it never reads as
    "there is nothing here yet, and here is how to change that". Both lines are
    needed — the fact, and the way out of it.
    """
    body = Text()
    body.append(f"  {title}\n", style="bold")
    body.append(f"  {fit(hint, max(10, width - 4)).rstrip()}", style="dim")
    return body
