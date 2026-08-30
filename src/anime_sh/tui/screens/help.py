"""A keybinding cheat-sheet modal, opened with `?` and dismissed with any key."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP = """[b]anime-sh — keys[/b]

[b]Move[/b]
  [cyan]↑ ↓[/cyan]  or  [cyan]j k[/cyan]     within a list
  [cyan]g[/cyan]   [cyan]G[/cyan]            first / last row
  [cyan]Tab[/cyan]              next section

[b]Act[/b]
  [cyan]Enter[/cyan]            open / play the highlighted item
  [cyan]/[/cyan]                search
  [cyan]Esc[/cyan]              clear the search, or go back
  [cyan]l[/cyan]                my AniList list
  [cyan]?[/cyan]                this help
  [cyan]q[/cyan]                quit

[dim]On the detail screen, Enter on an episode resolves and plays it;
episodes not yet aired are dimmed. Pick a different source in the
picker when a show is listed more than once.

Coming Up, on the right, appears on terminals 120 columns and wider, and
carries the shows you are caught up on — so Continue Watching keeps only
the episodes you can actually play.[/dim]

[dim]Press any key to close.[/dim]"""


class HelpScreen(ModalScreen):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: $background 60%; }
    HelpScreen #help-box {
        width: auto; max-width: 70; height: auto;
        padding: 1 3; border: round $accent; background: $panel;
    }
    """

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                yield Static(_HELP, id="help-box")

    def on_key(self) -> None:
        self.dismiss()

    def on_click(self) -> None:
        self.dismiss()
