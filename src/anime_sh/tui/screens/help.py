"""A keybinding cheat-sheet modal, opened with `?` and dismissed with any key.

Grouped by what you are trying to do rather than by which widget owns the key,
because nobody looking at this sheet knows which widget owns anything. The
action bar carries the handful of keys that matter where you are standing; this
is where the rest live, which is the progressive-disclosure half of the bargain —
`test_key_discoverability` fails the build if a key is bound and not written
down here.

Keys only. An earlier version closed with three paragraphs explaining how
shelves sample, how the detail screen resolves an episode, and what Coming Up
is for — forty-four rows of content in a forty-row terminal, so the modal
overflowed and lost its own "press any key to close" off the bottom. The notes
that survived are the two that change what a key *does*.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Middle, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP = """[b]anime-sh[/b]

[b]Move[/b]
  [cyan]↑ ↓[/cyan] [cyan]j k[/cyan]        within a shelf
  [cyan]← →[/cyan]            across the episode grid
  [cyan]g[/cyan] [cyan]G[/cyan]            first / last row
  [cyan]Tab[/cyan] [cyan]Shift+Tab[/cyan]  next / previous shelf
  [cyan]1[/cyan] [cyan]2[/cyan] [cyan]3[/cyan] [cyan]4[/cyan]        the shelves, as numbered on the rail

[b]See more[/b]
  [cyan]z[/cyan]              expand this shelf to the screen, or collapse it
  [cyan]v[/cyan]              denser view — home rows, or the episode grid

[b]Act[/b]
  [cyan]Enter[/cyan]          play or open whatever is highlighted
  [cyan]/[/cyan]              search
  [cyan]Esc[/cyan]            clear the search, or go back
  [cyan]n[/cyan]              next season, on a show
  [cyan]l[/cyan]              my AniList list
  [cyan]t[/cyan]              theme, previewed live — Esc keeps the old one
  [cyan]p[/cyan]              providers, the sources that get searched
  [cyan]?[/cyan]  [cyan]q[/cyan]           this help, and quit

[dim]A shelf shows a sample; its label counts the rest — "5 of 19".
Dimmed episodes have not aired yet.[/dim]

[dim]Press any key to close.[/dim]"""


class HelpScreen(ModalScreen):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: $background 60%; }
    /* Bounded and scrollable. The sheet is longer than a short terminal, and a
       modal that runs off the bottom of the screen takes its own instructions
       for getting out of it with it. */
    HelpScreen #help-scroll {
        width: auto; max-width: 70; height: auto; max-height: 100%;
        border: round $panel; background: $surface;
        scrollbar-size-vertical: 1;
    }
    HelpScreen #help-box { width: auto; height: auto; padding: 1 3; }
    """

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                with VerticalScroll(id="help-scroll"):
                    yield Static(_HELP, id="help-box")

    def on_key(self) -> None:
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()
