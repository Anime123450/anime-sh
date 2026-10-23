"""Source picker: list every provider entry that matches a show ("Title |
provider | N eps | sub/dub") and let the user choose which to play from.

Opened on purpose, from the detail screen (`s`), as a picker for *that* show:
choosing a row dismisses it with a `SourceChoice` and the detail screen pins it.
Nothing pushes a second detail screen. Esc dismisses with `None`.

The older flow — pushed in front of a show, then forwarding to a new detail
screen once a source was chosen — is kept only for callers that have not moved
to opening the detail screen directly (`pick=False`).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, LoadingIndicator, Static

from ...domain.models import Anime, Audio, SourceOption
from ..shell import ActionBar, TopBar, spaced
from ..widgets import SourceItem, source_columns, source_header


def _title_key(title: str) -> str:
    """A title as a comparison key: case, curly quotes and spacing ignored."""
    t = title.casefold().replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(t.split())


def _all_alike(sources) -> bool:
    """Whether every source is the same entry: same title, same episode count.

    A "[Mini]" batch beside the TV run, or a 12- beside a 24-episode entry, is a
    real choice and still gets the picker.
    """
    keys = {(_title_key(s.title), s.episode_count) for s in sources}
    return len(keys) == 1


@dataclass(frozen=True, slots=True)
class SourceChoice:
    """What the picker hands back: a pinned source (None = every provider) and
    the audio it should play in."""

    source: SourceOption | None
    audio: Audio


class SubFallbackItem(ListItem):
    """An explicit, per-title opt-in when no dub source was found."""

    def __init__(self) -> None:
        super().__init__(Label("Switch to Sub for this title only"))


class AutoSourceItem(ListItem):
    """Unpin: let playback try every provider and fall back between them."""

    def __init__(self) -> None:
        super().__init__(Label("Automatic [dim]· try every provider[/dim]"))


class SourcesScreen(Screen):
    BINDINGS = [("escape", "cancel", "Back")]

    def __init__(self, anime: Anime, *, resume_episode: float | None = None,
                 audio: Audio | None = None, pick: bool = False) -> None:
        super().__init__()
        self.anime = anime
        self.resume_episode = resume_episode
        self.audio = audio
        #: True when opened from the detail screen as a picker that dismisses
        #: with a `SourceChoice`; False for the legacy flow that forwards to a
        #: new detail screen.
        self.pick = pick

    # `CSS`, not `DEFAULT_CSS`: app.tcss caps every ListView at ten rows, and the
    # picker should use the screen it has.
    CSS = """
    SourcesScreen #sources-body { padding: 0 2; height: 1fr; }
    SourcesScreen #sources-status { width: 100%; color: $text-muted;
                                    margin: 0 0 1 0; }
    SourcesScreen #sources-head { width: 100%; height: 1; color: $text-muted;
                                  padding: 0 1 0 2; }
    SourcesScreen #sources { width: 100%; height: 1fr; max-height: 100000;
                             margin: 0; padding: 0; }
    SourcesScreen SourceItem { height: 1; }
    SourcesScreen SourceItem Static { height: 1; width: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Vertical(id="sources-body"):
            # `shelf-label`, not `section`: the redesign renamed that class and
            # this screen kept the old name, so its one heading rendered as
            # unstyled body text — the same size and weight as the rows under it.
            yield Label(spaced("Choose a source"), classes="shelf-label")
            yield LoadingIndicator(id="sources-loading")
            yield Static("", id="sources-status", markup=False)
            yield Static("", id="sources-head")
            yield ListView(id="sources")
        yield ActionBar(id="actionbar")

    def on_mount(self) -> None:
        self.title = self.anime.title.preferred
        if self.audio is None:
            self.audio = getattr(self.app, "audio", Audio.SUB)
        self.query_one("#sources-status", Static).display = False
        self.query_one("#sources-head", Static).display = False
        self._paint_shell()
        self.query_one("#sources", ListView).display = False
        self._load_sources()

    def _paint_shell(self) -> None:
        with contextlib.suppress(Exception):
            width = self.size.width or 100
            self.query_one("#topbar", TopBar).render_bar(
                self.anime.title.preferred, width, hint="pick a source")
            self.query_one("#actionbar", ActionBar).render_actions(
                width, zoom=False, contextual=(("↵", "choose"), ("esc", "back")))

    def _title_width(self) -> int:
        """Cells for the title column: whatever the row has after the fixed
        provider / episodes / audio columns, the gutter and the page padding."""
        return source_columns((self.size.width or 100) - 4)

    def on_resize(self, event) -> None:
        self._paint_shell()
        tw = source_columns(event.size.width - 4)
        with contextlib.suppress(Exception):
            self.query_one("#sources-head", Static).update(source_header(tw))
            for item in self.query(SourceItem):
                item.relayout(tw)

    def action_cancel(self) -> None:
        self.dismiss(None) if self.pick else self.app.pop_screen()

    @work(exclusive=True, group="sources")
    async def _load_sources(self) -> None:
        failed = False
        try:
            # Honor the app preference unless the user explicitly chose a
            # per-title audio override from the no-dub fallback.
            sources = await self.app.services.playback.list_sources(
                self.anime, audio=self.audio or Audio.SUB
            )
        except Exception as e:
            failed = True
            self.notify(f"Couldn't list sources: {e}", severity="error")
            sources = []

        self.query_one("#sources-loading").display = False
        lv = self.query_one("#sources", ListView)
        lv.display = True
        if not sources and not failed and self.audio is Audio.DUB:
            status = self.query_one("#sources-status", Static)
            status.update(
                "No English-dub source was found by the enabled providers. "
                "The app preference remains Dub; you can switch this title to Sub."
            )
            status.display = True
            lv.append(SubFallbackItem())
            lv.index = 0
            lv.focus()
            return

        if not self.pick:
            # Legacy flow. 0 or 1 match → skip the picker entirely. So do
            # several matches that are all the same entry on different
            # providers: that is not a choice. No pinned source then, so
            # playback keeps its fan-out.
            if len(sources) <= 1 or _all_alike(sources):
                self._forward(sources[0] if len(sources) == 1 else None)
                return
        elif not sources:
            status = self.query_one("#sources-status", Static)
            status.update("No provider lists this title right now."
                          if not failed else "Couldn't reach the providers.")
            status.display = True

        head = self.query_one("#sources-head", Static)
        tw = self._title_width()
        head.update(source_header(tw))
        head.display = bool(sources)
        if self.pick:
            lv.append(AutoSourceItem())
        for source in sources:
            lv.append(SourceItem(source, title_w=tw))
        lv.index = 0
        lv.focus()

    def _forward(self, source: SourceOption | None) -> None:
        from .detail import DetailScreen

        self.app.pop_screen()
        self.app.push_screen(
            DetailScreen(
                self.anime,
                resume_episode=self.resume_episode,
                source=source,
                audio=source.audio if source else self.audio,
            )
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SubFallbackItem):
            # Per title, never global: the app's audio preference is untouched.
            if self.pick:
                self.dismiss(SourceChoice(None, Audio.SUB))
            else:
                self.app.pop_screen()
                self.app.push_screen(SourcesScreen(
                    self.anime, resume_episode=self.resume_episode, audio=Audio.SUB,
                ))
        elif isinstance(item, AutoSourceItem):
            self.dismiss(SourceChoice(None, self.audio or Audio.SUB))
        elif isinstance(item, SourceItem):
            if self.pick:
                self.dismiss(SourceChoice(item.source, item.source.audio))
            else:
                self._forward(item.source)
