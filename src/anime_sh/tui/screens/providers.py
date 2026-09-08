"""The provider picker.

Which sources anime-sh is allowed to search was previously only reachable
through `anime providers enable|disable` — a thing you had to know existed. It
is the setting most worth reaching when something is not playing, so it belongs
on a key like the theme picker.

Toggling writes `providers.disabled` and takes effect on the next launch: the
provider list is resolved once, when the container is built, and pretending
otherwise would show a provider as live while nothing was using it.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class ProviderItem(ListItem):
    """One installed provider: whether it is on, its name, and its priority."""

    def __init__(self, name: str, priority: int, enabled: bool) -> None:
        self.provider_name = name
        self.enabled = enabled
        super().__init__(Label(self._text()))

    def _text(self) -> str:
        # A shape, not just a colour: the difference has to survive a terminal
        # with no colour, which is the same rule the episode glyphs follow.
        mark = "[green]●[/green]" if self.enabled else "[dim]○[/dim]"
        state = "" if self.enabled else "  [dim]off[/dim]"
        return f"{mark}  {self.provider_name:<14}{state}"

    def refresh_text(self) -> None:
        self.query_one(Label).update(self._text())


class ProvidersScreen(ModalScreen):
    """See which sources are in use, and switch them on and off."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("space", "toggle", "Toggle", show=False),
    ]

    DEFAULT_CSS = """
    ProvidersScreen { align: center middle; }
    ProvidersScreen > #providers-box {
        width: 46; height: auto; max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: round $panel;
    }
    ProvidersScreen #providers-title { text-style: bold; padding: 0 0 1 0; }
    ProvidersScreen #providers-hint { color: $text-muted; padding: 1 0 0 0; }
    ProvidersScreen ListView { background: $surface; height: auto; max-height: 14; }
    ProvidersScreen ListView > ListItem { background: $surface; padding: 0 1; }
    ProvidersScreen ListView > ListItem.-highlight { background: $primary 40%; }
    """

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="providers-box"):
            yield Label("Providers", id="providers-title")
            yield ListView(id="providers-list")
            yield Static(
                "⏎ or space toggles · applies next launch · esc close",
                id="providers-hint",
            )

    def on_mount(self) -> None:
        lv = self.query_one("#providers-list", ListView)
        for name, priority, enabled in self._installed():
            lv.append(ProviderItem(name, priority, enabled))
        lv.focus()

    @staticmethod
    def _installed() -> list[tuple[str, int, bool]]:
        """Installed providers, highest priority first, with their on/off state.

        Best-effort: a plugin that fails to import must not take the picker down
        with it — the picker is where you would go to turn that plugin off.
        """
        try:
            from ...config import load_config
            from ...infra import registry

            disabled = set(load_config().providers.disabled)
            found = [
                (p.name, getattr(p, "priority", 0), p.name not in disabled)
                for p in registry.load_providers()
            ]
        except Exception:
            return []
        return sorted(found, key=lambda row: (-row[1], row[0]))

    def action_cursor_down(self) -> None:
        self.query_one("#providers-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#providers-list", ListView).action_cursor_up()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_toggle(self) -> None:
        lv = self.query_one("#providers-list", ListView)
        item = lv.highlighted_child
        if isinstance(item, ProviderItem):
            self._toggle(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ProviderItem):
            self._toggle(event.item)

    def _toggle(self, item: ProviderItem) -> None:
        items = [i for i in self.query(ProviderItem)]
        going_off = item.enabled
        if going_off and sum(1 for i in items if i.enabled) <= 1:
            # The CLI refuses this too. A configuration with every provider off
            # cannot find a single episode, and the failure would show up later
            # as "no sources" rather than here as a choice.
            self.app.notify(
                f"{item.provider_name} is the last provider left — "
                "turning it off would leave nothing able to find episodes.",
                severity="warning",
            )
            return
        item.enabled = not item.enabled
        item.refresh_text()
        self._persist(items)

    def _persist(self, items: list[ProviderItem]) -> None:
        """Write the whole disabled list, not a delta.

        The config field is the set of *off* providers, so it is rewritten from
        what is on screen — a delta would drift the moment a provider was
        installed or removed between launches.
        """
        disabled = sorted(i.provider_name for i in items if not i.enabled)
        try:
            from ...config import set_config_value

            set_config_value("providers.disabled", ",".join(disabled))
        except Exception as e:
            self.app.notify(f"Couldn't save providers: {e}", severity="warning")
