"""Settings: every user-facing preference on one screen, in four tabs.

Appearance, Playback, Sources and Downloads used to be three different places —
`t` opened a theme modal, `p` a provider modal, and the rest was `anime config
set` or nothing. They are tabs of one screen now; `t` and `p` still work and land
on the tab they always meant.

Every value is read through `SettingsService`, which knows where it came from.
A value forced by a command-line flag or an `ANIME_SH_*` variable is shown with
its source and locked: editing it would change the file and nothing you could
see. Every write goes through the same service, so a widget never touches TOML,
and a failed save leaves the old value both on screen and on disk.

When things apply: playback settings on the next episode you start, the download
folder on the next download, sources on the next launch (the provider list is
built once at start-up). The labels say so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView, Static

from ...app.settings import SettingInfo, SettingsService
from ..themes import available

CATEGORIES: tuple[str, ...] = ("appearance", "playback", "sources", "downloads")
_TITLES = {
    "appearance": "Appearance",
    "playback": "Playback",
    "sources": "Sources",
    "downloads": "Downloads",
}

#: The fields each tab edits, in display order, with their row labels.
_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "appearance": (
        ("ui.density", "Home density"),
        ("ui.episodes", "Episode layout"),
    ),
    "playback": (
        ("player.name", "Player"),
        ("player.vlc_path", "VLC path"),
        ("playback.audio", "Audio"),
        ("playback.quality", "Quality"),
        ("playback.auto_next", "Auto-next"),
        ("playback.skip_intro", "Skip intros"),
        ("playback.skip_outro", "Skip outros"),
        ("playback.prefer_downloads", "Prefer downloads"),
    ),
    "downloads": (("downloads.dir", "Download folder"),),
}

#: Free-text fields; everything else is a toggle or a fixed list of choices.
_TEXT_KEYS = frozenset({"player.vlc_path", "downloads.dir"})

_ABOUT = {
    "ui.density": "How tightly the home shelves pack. Same as `v` on the home screen.",
    "ui.episodes": "How a show's episodes are laid out. Same as `v` on a show.",
    "player.name": "Which player opens episodes. Applies to the next episode you start.",
    "player.vlc_path": "Where vlc(.exe) is. Leave empty to find it automatically.",
    "playback.audio": "Subbed or dubbed, where a source has both.",
    "playback.quality": "Preferred stream quality; `best` takes the highest offered.",
    "playback.auto_next": "Start the next episode when one finishes.",
    "playback.skip_intro": "Jump past the opening when its timing is known.",
    "playback.skip_outro": "Jump past the ending when its timing is known.",
    "playback.prefer_downloads": "Play a downloaded copy instead of streaming.",
    "downloads.dir": "Where downloads are saved. Applies to the next download.",
}

_HINT = "←→ tab · ↑↓ move · ↵ change · esc back"
_EDIT_HINT = "type a value · ↵ save · esc cancel"


def _label(text: str) -> Label:
    # Text, not markup: a path or a value with brackets in it must show as typed.
    return Label(Text(text))


class _Header(ListItem):
    """A section heading inside the list. Disabled, so the cursor skips it."""

    def __init__(self, text: str) -> None:
        super().__init__(_label(text), disabled=True, classes="-header")


class SettingRow(ListItem):
    """One config field: its label, its effective value, and why it is locked."""

    def __init__(
        self, key: str, label: str, info: SettingInfo, choices: tuple[str, ...] = ()
    ) -> None:
        self.key = key
        self.label_text = label
        self.info = info
        self.choices = choices
        if isinstance(info.value, bool):
            self.kind = "bool"
        elif key in _TEXT_KEYS:
            self.kind = "text"
        else:
            self.kind = "choice"
        super().__init__(_label(self.text), disabled=info.read_only)

    @property
    def text(self) -> str:
        value = self.info.value
        if isinstance(value, bool):
            shown = "on" if value else "off"
        elif value in ("", None):
            shown = "auto-detect" if self.key == "player.vlc_path" else "(not set)"
        else:
            shown = str(value)
        line = f"{self.label_text:<18}{shown}"
        if self.info.read_only:
            line += f"   locked · set by {self.info.source}"
        return line

    def update_info(self, info: SettingInfo) -> None:
        self.info = info
        self.disabled = info.read_only
        self.query_one(Label).update(Text(self.text))


class ToggleRow(ListItem):
    """One installed provider or resolver, on or off."""

    def __init__(
        self, group: str, name: str, enabled: bool, *, priority: int | None = None,
        locked: bool = False,
    ) -> None:
        self.group = group
        self.name_ = name
        self.enabled = enabled
        self.priority = priority
        super().__init__(_label(self.text), disabled=locked)

    @property
    def text(self) -> str:
        # A shape as well as a word: the state has to survive a terminal with
        # no colour, the same rule the episode glyphs follow.
        mark = "●" if self.enabled else "○"
        return f"{mark}  {self.name_:<16}{'' if self.enabled else 'off'}"

    def refresh_text(self) -> None:
        self.query_one(Label).update(Text(self.text))


class ThemeRow(ListItem):
    """One theme, with a swatch of the colours that carry the design."""

    def __init__(self, name: str, theme, *, locked: bool = False) -> None:
        self.theme_name = name
        # The three tiers plus the accent — the four slots the home screen's
        # depth and focus marker are built from.
        swatch = "".join(
            f"[{colour}]██[/{colour}]"
            for colour in (theme.background, theme.surface, theme.panel, theme.accent)
        )
        kind = "Dark" if theme.dark else "Light"
        super().__init__(
            Label(f"{swatch}  {name:<18}[dim]{kind}[/dim]"), disabled=locked
        )


class SettingsScreen(Screen):
    """Every setting, by category, saved through `SettingsService`."""

    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("left", "prev_category", "Previous tab", show=False),
        Binding("right", "next_category", "Next tab", show=False),
        # A screen with tabs answering Tab: it only moved focus to nowhere.
        Binding("tab", "next_category", "Next tab", show=False),
        Binding("shift+tab", "prev_category", "Previous tab", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("space", "activate_highlighted", "Change", show=False),
    ]

    DEFAULT_CSS = """
    SettingsScreen { layout: vertical; }
    SettingsScreen #settings-title { height: 1; padding: 0 2; text-style: bold; }
    SettingsScreen #settings-tabs { height: 1; padding: 0 1; margin: 1 0 1 0; }
    SettingsScreen #settings-list { height: 1fr; background: $background; }
    SettingsScreen #settings-list > ListItem { background: $background; padding: 0 2; }
    SettingsScreen #settings-list > ListItem.-header {
        color: $text-muted; text-style: bold; padding: 1 1 0 1;
    }
    SettingsScreen #settings-list > ListItem.-highlight { background: $primary 40%; }
    SettingsScreen #settings-input { display: none; margin: 0 1; }
    SettingsScreen #settings-input.-editing { display: block; }
    SettingsScreen #settings-detail {
        height: auto; min-height: 2; padding: 0 2; color: $text-muted;
        border-top: solid $panel;
    }
    SettingsScreen #settings-detail.-error { color: $error; }
    SettingsScreen #settings-hint { height: 1; padding: 0 2; color: $text-muted; }
    """

    def __init__(self, category: str = "appearance") -> None:
        if category not in CATEGORIES:
            raise ValueError(f"unknown settings category {category!r}")
        super().__init__()
        self.category = category
        #: The theme that is actually saved. Anything else on screen is a preview.
        self._saved_theme: str | None = None
        self._editing: SettingRow | None = None
        self.detail_text = ""

    # -- plumbing ------------------------------------------------------------ #
    @property
    def _svc(self) -> SettingsService:
        return self.app.settings_service

    def compose(self) -> ComposeResult:
        yield Static("Settings", id="settings-title")
        yield Static(id="settings-tabs")
        yield ListView(id="settings-list")
        yield Input(id="settings-input")
        yield Static(id="settings-detail")
        yield Static(_HINT, id="settings-hint")

    async def on_mount(self) -> None:
        self._saved_theme = self.app.theme
        await self._populate()

    def _list(self) -> ListView:
        return self.query_one("#settings-list", ListView)

    def _show(self, text: str, *, error: bool = False) -> None:
        self.detail_text = text
        detail = self.query_one("#settings-detail", Static)
        detail.update(Text(text))
        detail.set_class(error, "-error")

    # -- tabs ----------------------------------------------------------------- #
    def _render_tabs(self) -> None:
        parts = []
        for cat in CATEGORIES:
            style = "bold reverse" if cat == self.category else "dim"
            parts.append(
                f"[@click=screen.show_category('{cat}')][{style}] {_TITLES[cat]} [/][/]"
            )
        self.query_one("#settings-tabs", Static).update(" ".join(parts))

    def show_category(self, category: str) -> None:
        if category not in CATEGORIES:
            return
        if self._editing is not None:
            self._end_edit()
        self._revert_preview()
        self.category = category
        self._render_tabs()
        self.run_worker(self._populate(), exclusive=True, group="settings-tab")

    def action_show_category(self, category: str) -> None:
        self.show_category(category)

    def _step(self, delta: int) -> None:
        i = CATEGORIES.index(self.category)
        self.show_category(CATEGORIES[(i + delta) % len(CATEGORIES)])

    def action_next_category(self) -> None:
        self._step(1)

    def action_prev_category(self) -> None:
        self._step(-1)

    # -- building a tab --------------------------------------------------------- #
    def _field_rows(self, category: str) -> list[ListItem]:
        rows: list[ListItem] = []
        for key, label in _FIELDS.get(category, ()):
            try:
                info = self._svc.inspect(key)
                choices = self._svc.choices(key)
            except Exception as e:
                rows.append(_Header(f"{label}: unreadable ({e})"))
                continue
            rows.append(SettingRow(key, label, info, choices))
        return rows

    def _build(self, category: str) -> list[ListItem]:
        if category == "appearance":
            items: list[ListItem] = [_Header("Layout")]
            items += self._field_rows("appearance")
            try:
                locked = self._svc.inspect("ui.theme").read_only
            except Exception:
                locked = False
            title = "Theme — locked by an override" if locked else (
                "Theme — ↑↓ previews · ↵ keeps · esc puts it back")
            items.append(_Header(title))
            for name in available(self.app):
                items.append(ThemeRow(name, self.app.get_theme(name), locked=locked))
            return items
        if category == "sources":
            return self._source_rows()
        if category == "playback":
            return [_Header("Player — applies to the next episode you start"),
                    *self._field_rows("playback")]
        return [_Header("Downloads — applies to the next download"),
                *self._field_rows("downloads")]

    def _source_rows(self) -> list[ListItem]:
        items: list[ListItem] = []
        for group, label in (("providers", "Providers"), ("resolvers", "Resolvers")):
            source = "default"
            try:
                info = self._svc.inspect(f"{group}.disabled")
                off, locked, source = set(info.value), info.read_only, info.source
            except Exception:
                off, locked = set(), False
            note = f"locked · set by {source}" if locked else "applies next launch"
            items.append(_Header(f"{label} — {note}"))
            if group == "providers":
                for name, priority in self._installed_providers():
                    items.append(ToggleRow(group, name, name not in off,
                                           priority=priority, locked=locked))
            else:
                for name in self._installed_resolvers():
                    items.append(ToggleRow(group, name, name not in off, locked=locked))
        return items

    @staticmethod
    def _installed_providers() -> list[tuple[str, int]]:
        """Installed providers, highest priority first.

        Best-effort: a plugin that fails to import must not take this tab down
        with it — this tab is where you would go to turn that plugin off.
        """
        try:
            from ...infra import registry

            found = [(p.name, getattr(p, "priority", 0))
                     for p in registry.load_providers()]
        except Exception:
            return []
        return sorted(found, key=lambda row: (-row[1], row[0]))

    @staticmethod
    def _installed_resolvers() -> list[str]:
        """Installed resolver names, read from their entry points.

        Names only, without constructing them: a resolver may need the HTTP
        client the container builds, and switching one off should not depend
        on being able to build it.
        """
        try:
            from importlib.metadata import entry_points

            from ...infra.registry import RESOLVER_GROUP

            return sorted({ep.name for ep in entry_points(group=RESOLVER_GROUP)})
        except Exception:
            return []

    async def _populate(self) -> None:
        lv = self._list()
        await lv.clear()
        items = self._build(self.category)
        await lv.extend(items)
        self._render_tabs()
        target = None
        if self.category == "appearance":
            target = next((i for i in items if isinstance(i, ThemeRow)
                           and i.theme_name == self._saved_theme and not i.disabled),
                          None)
        if target is None:
            target = next((i for i in items if not i.disabled), None)
        lv.index = items.index(target) if target is not None else None
        lv.focus()
        self._describe(target)

    # -- describing the row under the cursor ---------------------------------- #
    def _describe(self, item: ListItem | None) -> None:
        if isinstance(item, ThemeRow):
            self._show(f"{item.theme_name} — previewing. ↵ keeps it; esc goes back "
                       f"to {self._saved_theme}.")
        elif isinstance(item, ToggleRow):
            what = "provider" if item.group == "providers" else "resolver"
            prio = f" · priority {item.priority}" if item.priority is not None else ""
            self._show(f"{item.name_}: {what} {'on' if item.enabled else 'off'}{prio}. "
                       "↵ or space toggles; takes effect next launch.")
        elif isinstance(item, SettingRow):
            text = _ABOUT.get(item.key, "")
            if item.key == "player.name":
                text += "\n" + self._player_status()
            elif item.key == "downloads.dir" and item.info.value:
                text += f"\nNow: {Path(str(item.info.value)).expanduser()}"
            self._show(text)
        else:
            self._show("")

    def _player_status(self) -> str:
        """What each player resolves to on this machine, without starting one."""
        players = getattr(self.app.services, "players", None)
        if players is None:
            return "Availability is checked when playback starts."
        try:
            cfg = self._svc.snapshot()
            lines = []
            for name in ("mpv", "vlc"):
                override = cfg.player.vlc_path if name == "vlc" else ""
                info = players.status(name, path_override=override)
                lines.append(f"{name}: {info.path}" if info.available
                             else f"{name}: not found — {info.detail}")
            return " · ".join(lines)
        except Exception as e:
            return f"Couldn't check the players: {e}"

    # -- theme preview ---------------------------------------------------------- #
    def _revert_preview(self) -> None:
        if self._saved_theme and self.app.theme != self._saved_theme:
            try:
                self.app.theme = self._saved_theme
            except Exception:
                pass

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, ThemeRow) and not item.disabled:
            try:
                self.app.theme = item.theme_name
            except Exception:
                pass  # a theme that will not apply must not trap you here
        else:
            self._revert_preview()
        self._describe(item)

    # -- changing things --------------------------------------------------------- #
    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        await self.activate(event.item)

    async def action_activate_highlighted(self) -> None:
        await self.activate(self._list().highlighted_child)

    def action_cursor_down(self) -> None:
        self._list().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._list().action_cursor_up()

    async def activate(self, item: ListItem | None) -> None:
        """Change the setting ``item`` stands for, the way its kind is changed."""
        if item is None:
            return
        if item.disabled:
            if isinstance(item, SettingRow) and item.info.read_only:
                self._show(f"{item.key} is set by {item.info.source} for this run; "
                           "change it there.", error=True)
            return
        if isinstance(item, ThemeRow):
            self._save_theme(item.theme_name)
        elif isinstance(item, ToggleRow):
            self._toggle(item)
        elif isinstance(item, SettingRow):
            if item.kind == "bool":
                self._save_row(item, "false" if item.info.value else "true")
            elif item.kind == "choice" and item.choices:
                current = str(item.info.value)
                i = item.choices.index(current) if current in item.choices else -1
                self._save_row(item, item.choices[(i + 1) % len(item.choices)])
            else:
                self._start_edit(item)

    def _save_theme(self, name: str) -> None:
        try:
            self._svc.update("ui.theme", name)
        except Exception as e:
            self._revert_preview()
            self._show(f"Couldn't save the theme: {e}", error=True)
            return
        self._saved_theme = name
        self.app.theme = name
        self._show(f"Theme set to {name}.")

    def _save_row(self, row: SettingRow, raw: str) -> bool:
        """Persist one value; on any failure show why and keep the old one."""
        try:
            info = self._svc.update(row.key, raw)
        except Exception as e:
            self._show(f"Couldn't save {row.label_text.lower()}: {e}", error=True)
            return False
        row.update_info(info)
        self._apply_now(row.key, info.value)
        self._describe(row)
        return True

    def _apply_now(self, key: str, value: Any) -> None:
        """Runtime effects of a saved value, only ever after the save worked."""
        if key == "playback.audio":
            from ...domain.models import Audio

            self.app.audio = Audio.DUB if value == "dub" else Audio.SUB
        for screen in self.app.screen_stack:
            hook = getattr(screen, "apply_setting", None)
            if hook is not None and screen is not self:
                try:
                    hook(key, value)
                except Exception:
                    pass

    def _toggle(self, item: ToggleRow) -> None:
        group = [r for r in self.query(ToggleRow) if r.group == item.group]
        if (item.group == "providers" and item.enabled
                and sum(1 for r in group if r.enabled) <= 1):
            # The CLI refuses this too: with every provider off nothing can find
            # an episode, and the failure would surface later as "no sources".
            self._show(f"{item.name_} is the last provider left — turning it off "
                       "would leave nothing able to find episodes. Enable another "
                       "first; changes apply next launch.", error=True)
            return
        item.enabled = not item.enabled
        # The whole off-list, not a delta: it is rewritten from what is on screen
        # so it cannot drift as plugins are installed and removed.
        off = sorted(r.name_ for r in group if not r.enabled)
        try:
            self._svc.update(f"{item.group}.disabled", ",".join(off))
        except Exception as e:
            item.enabled = not item.enabled
            self._show(f"Couldn't save {item.group}: {e}", error=True)
            return
        item.refresh_text()
        self._describe(item)

    # -- free-text fields ------------------------------------------------------ #
    def _start_edit(self, row: SettingRow) -> None:
        self._editing = row
        box = self.query_one("#settings-input", Input)
        box.value = str(row.info.value or "")
        box.placeholder = row.label_text
        box.add_class("-editing")
        box.focus()
        self.query_one("#settings-hint", Static).update(_EDIT_HINT)

    def _end_edit(self) -> None:
        self._editing = None
        box = self.query_one("#settings-input", Input)
        box.remove_class("-editing")
        self.query_one("#settings-hint", Static).update(_HINT)
        self._list().focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._editing is not None:
            await self.save_text(self._editing, event.value)

    async def save_text(self, row: SettingRow, value: str) -> bool:
        """Validate and save a typed value. The folder is not created here —
        a download job makes it when it needs it."""
        text = value.strip()
        if "\x00" in value:
            self._show("That value contains a NUL character.", error=True)
            return False
        if row.key == "downloads.dir" and not text:
            self._show("The download folder can't be empty.", error=True)
            return False
        if not self._save_row(row, text):
            return False
        if self._editing is row:
            self._end_edit()
        if row.key == "downloads.dir":
            self._show(f"Downloads will go to {Path(text).expanduser()} — the "
                       "folder is created when the next download starts.")
        return True

    def action_close(self) -> None:
        if self._editing is not None:
            row = self._editing
            self._end_edit()
            self._describe(row)
            return
        self._revert_preview()
        self.app.pop_screen()
