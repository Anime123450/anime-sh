"""A keybinding cheat-sheet modal, opened with `?` and dismissed with any key.

Grouped by what you are trying to do rather than by which widget owns the key,
because nobody looking at this sheet knows which widget owns anything. The
action bar carries the handful of keys that matter where you are standing; this
is where the rest live, which is the progressive-disclosure half of the bargain —
`test_key_discoverability` fails the build if a key is bound and not written
down here, and if the sheet names one that is not bound.

Keys only. An earlier version closed with three paragraphs explaining how
shelves sample, how the detail screen resolves an episode, and what Coming Up
is for — forty-four rows of content in a forty-row terminal, so the modal
overflowed and lost its own "press any key to close" off the bottom.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Middle, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP = """[b $background on $primary] ▶ anime-sh [/]

[$primary]▍[/] [b]Move[/b]
  [b $accent]← →[/] [b $accent]h[/]          along a shelf
  [b $accent]↑ ↓[/] [b $accent]j k[/]        between shelves
  [b $accent]Home[/] [b $accent]End[/]       first / last poster on this shelf
  [b $accent]g[/] [b $accent]G[/]            first / last shelf
  [b $accent]Tab[/] [b $accent]Shift+Tab[/]  next / previous shelf
  [b $accent]1[/] [b $accent]2[/] [b $accent]3[/] [b $accent]4[/]        Continue · Favourites · Season · Trending

[$primary]▍[/] [b]Act[/b]
  [b $accent]Enter[/]          play or open whatever is highlighted
  [b $accent]/[/]              search
  [b $accent]Esc[/]            clear the search, or go back
  [b $accent]v[/]              denser view: shelves, or the episode grid
  [b $accent]n[/]              next season, on a show
  [b $accent]l[/]              my AniList list
  [b $accent],[/]              settings — appearance, playback, sources
  [b $accent]t[/]  [b $accent]p[/]           settings at the theme / the sources
  [b $accent]?[/]  [b $accent]q[/]           this help, and quit
  [b $accent]Ctrl+P[/]         command palette

[dim]The large panel at the top always describes the poster under the cursor.
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
        scrollbar-background: $surface;
        scrollbar-color: $panel;
    }
    HelpScreen #help-box { width: 1fr; height: auto; padding: 1 3; }
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
