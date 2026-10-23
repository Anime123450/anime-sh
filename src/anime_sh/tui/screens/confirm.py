"""Confirmation modal screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Modal dialog asking the user to confirm an action."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }
    #confirm-dialog {
        width: 54;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    #confirm-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    #confirm-prompt {
        width: 100%;
        margin-bottom: 1;
    }
    #confirm-buttons {
        width: 100%;
        height: auto;
        align: center middle;
    }
    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("enter,y", "confirm", "Confirm", show=False),
        Binding("escape,n,q", "cancel", "Cancel", show=False),
    ]

    def __init__(self, prompt: str, *, title: str = "Confirm") -> None:
        super().__init__()
        self.prompt = prompt
        self.dialog_title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self.dialog_title, id="confirm-title", markup=True)
            yield Static(self.prompt, id="confirm-prompt", markup=True)
            with Center(id="confirm-buttons"):
                yield Button("Yes (Enter)", variant="primary", id="confirm-yes")
                yield Button("No (Esc)", variant="default", id="confirm-no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)
