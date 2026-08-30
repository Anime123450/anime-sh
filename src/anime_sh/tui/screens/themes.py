"""The theme picker.

Live: moving the cursor applies the theme to the whole app at once, so you are
choosing by looking at anime-sh rather than at a list of names. Enter keeps the
one you are on and writes it to the config; Escape puts back the one you arrived
with, so browsing is free.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from ..themes import available


class ThemeItem(ListItem):
    """One theme, with a swatch of the colours that actually carry the design."""

    def __init__(self, name: str, theme) -> None:
        self.theme_name = name
        # The three tiers plus the accent — the four slots the home screen's
        # depth and focus marker are built from. A swatch of primary/secondary
        # would look prettier and tell you less about how the screen will read.
        swatch = "".join(
            f"[{colour}]██[/{colour}]"
            for colour in (theme.background, theme.surface, theme.panel, theme.accent)
        )
        label = "Light" if not theme.dark else "Dark"
        super().__init__(Label(f"{swatch}  {name:<18}[dim]{label}[/dim]"))


class ThemesScreen(ModalScreen):
    """Pick a theme, seeing it applied as you move."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    /* The translucency that lets the preview show through is set in `app.tcss`,
       not here: this file's `Screen { background: $background; }` is app-level
       CSS, which outranks a widget's DEFAULT_CSS — so a rule here was silently
       overridden and the modal came out opaque. */
    ThemesScreen { align: center middle; }
    ThemesScreen > #theme-box {
        width: 46;
        /* Offset from dead centre so the modal sits over the emptier right of
           the screen rather than on top of the rows being previewed. */
        offset: 25% 0;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: round $panel;
    }
    ThemesScreen #theme-title { text-style: bold; padding: 0 0 1 0; }
    ThemesScreen #theme-hint { color: $text-muted; padding: 1 0 0 0; }
    ThemesScreen ListView { background: $surface; height: auto; max-height: 16; }
    ThemesScreen ListView > ListItem { background: $surface; padding: 0 1; }
    ThemesScreen ListView > ListItem.-highlight { background: $primary 40%; }
    """

    def __init__(self) -> None:
        super().__init__()
        # What to go back to if this is cancelled. Captured before anything is
        # previewed, or Escape restores whichever theme was last hovered.
        self._original: str | None = None

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="theme-box"):
            yield Label("Theme", id="theme-title")
            yield ListView(id="theme-list")
            yield Static("↑↓ preview · ⏎ keep · esc cancel", id="theme-hint")

    def on_mount(self) -> None:
        self._original = self.app.theme
        lv = self.query_one("#theme-list", ListView)
        names = available(self.app)
        for name in names:
            lv.append(ThemeItem(name, self.app.get_theme(name)))
        if self._original in names:
            lv.index = names.index(self._original)
        lv.focus()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Apply as you move — the preview *is* the app."""
        item = event.item
        if isinstance(item, ThemeItem):
            try:
                self.app.theme = item.theme_name
            except Exception:
                pass  # a theme that will not apply must not trap you in here

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, ThemeItem):
            return
        self.app.theme = item.theme_name
        self._persist(item.theme_name)
        self.dismiss(item.theme_name)

    def action_cursor_down(self) -> None:
        self.query_one("#theme-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#theme-list", ListView).action_cursor_up()

    def action_cancel(self) -> None:
        if self._original:
            try:
                self.app.theme = self._original
            except Exception:
                pass
        self.dismiss(None)

    def _persist(self, name: str) -> None:
        """Write the choice to the config file.

        A theme chosen in the app and gone again next launch is worse than no
        picker at all — it reads as the setting not having worked. Failure is
        reported rather than swallowed: it is the user's choice that was lost.
        """
        try:
            from ...config import set_config_value

            set_config_value("ui.theme", name)
        except Exception as e:
            self.app.notify(f"Couldn't save the theme: {e}", severity="warning")
