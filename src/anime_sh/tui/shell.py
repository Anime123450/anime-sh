"""The application shell — the frame every screen sits inside.

Two pieces of furniture that never move, so spatial memory does the navigating:
a top bar that says where you are, and an action bar that says what the thing
under the cursor can do.

Neither holds state of its own. Both are told what is focused and are pure
renders of that. It is deliberate — navigation lives in one place (the screen),
and the furniture reflects it rather than competing to own it.

There was a third: a nav rail down the left, numbering the four sections. It
went with the stacked lists it mapped. Twenty columns of permanent labels was
orientation worth having beside three tall lists of text; beside horizontal
shelves it is twenty columns taken off every shelf to repeat what the shelf
headings already say, and a shelf is measured in posters.
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
    """One destination: a shelf on the home screen, and the label above it."""

    label: str  #: as the heading writes it
    list_id: str  #: the Shelf it governs
    head_id: str  #: that shelf's heading Label


#: Ordered by what you most likely came for, and numbered in that order by the
#: digit keys. Continue Watching is the reason the app exists; Trending is where
#: you land when the library is empty.
SECTIONS: tuple[Section, ...] = (
    Section("Continue", "#continue", "#sec-continue"),
    Section("Favourites", "#favorites", "#sec-favorites"),
    Section("This Season", "#seasonal", "#sec-seasonal"),
    Section("Trending", "#trending", "#sec-trending"),
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


#: How a key is written on the bars, against the name Textual knows it by.
#: Only the ones that differ: a bar says `esc`, the binding says `escape`.
_KEY_NAMES = {"↵": "enter", "esc": "escape", "?": "question_mark"}


def clickable(markup: str, action: str) -> str:
    """Wrap rendered markup in a Textual click action.

    The bars are `Static`s, and a Static does nothing when you click it.
    Textual's own `Footer` does — `FooterKey.on_mouse_down` calls
    `simulate_key` — so replacing the footer with a prettier Static quietly took
    away every mouse affordance the app had. Markup actions give them back
    without any coordinate arithmetic: the span knows its own extent.
    """
    return f"[@click={action}]{markup}[/]"


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
            out.append(clickable(
                f"[$accent]{key}[/] [dim]{label}[/dim]",
                f"app.press_key({_KEY_NAMES.get(key, key)!r})",
            ))
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
