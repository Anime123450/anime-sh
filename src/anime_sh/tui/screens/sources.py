"""Source picker: when a show matches more than one provider entry, list them
all ("Title — provider · N eps") and let the user choose which to play from,
instead of the app guessing. A single match forwards straight to the detail
screen; no match falls back to the fan-out.
"""

from __future__ import annotations

import contextlib

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, LoadingIndicator, Static

from ...domain.models import Anime, Audio
from ..shell import ActionBar, TopBar, spaced
from ..widgets import SourceItem
from .detail import DetailScreen


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


class SubFallbackItem(ListItem):
    """An explicit, per-title opt-in when no dub source was found."""

    def __init__(self) -> None:
        super().__init__(Label("Switch to Sub for this title only"))


class SourcesScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, anime: Anime, *, resume_episode: float | None = None,
                 audio: Audio | None = None) -> None:
        super().__init__()
        self.anime = anime
        self.resume_episode = resume_episode
        self.audio = audio

    DEFAULT_CSS = """
    SourcesScreen #sources-body {
        padding: 0 2;
        align: center top;
    }
    SourcesScreen #sources-status {
        width: 1fr;
        max-width: 110;
        color: $text-muted;
        margin: 0 0 1 0;
    }
    SourcesScreen #sources {
        width: 1fr;
        max-width: 110;
    }
    """

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with VerticalScroll(id="sources-body"):
            # `shelf-label`, not `section`: the redesign renamed that class and
            # this screen kept the old name, so its one heading rendered as
            # unstyled body text — the same size and weight as the rows under it.
            yield Label(spaced("Choose a source"), classes="shelf-label")
            yield LoadingIndicator(id="sources-loading")
            yield Static("", id="sources-status", markup=False)
            yield ListView(id="sources")
        yield ActionBar(id="actionbar")

    def on_mount(self) -> None:
        self.title = self.anime.title.preferred
        if self.audio is None:
            self.audio = getattr(self.app, "audio", Audio.SUB)
        self.query_one("#sources-status", Static).display = False
        with contextlib.suppress(Exception):
            width = self.size.width or 100
            self.query_one("#topbar", TopBar).render_bar(
                self.anime.title.preferred, width, hint="pick a source")
            self.query_one("#actionbar", ActionBar).render_actions(
                width, zoom=False, contextual=(("↵", "choose"), ("esc", "back")))
        self.query_one("#sources", ListView).display = False
        self._load_sources()

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

        # 0 or 1 match → skip the picker entirely. So do several matches that
        # are all the same entry on different providers: that is not a choice,
        # and asking it on every show made the picker a second click on
        # everything. No pinned source then, so playback keeps its fan-out and
        # falls back to the next provider when one is down.
        if len(sources) <= 1 or _all_alike(sources):
            self.app.pop_screen()
            self.app.push_screen(
                DetailScreen(
                    self.anime,
                    resume_episode=self.resume_episode,
                    source=sources[0] if len(sources) == 1 else None,
                    audio=self.audio,
                )
            )
            return

        for source in sources:
            lv.append(SourceItem(source))
        lv.index = 0
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SubFallbackItem):
            self.app.pop_screen()
            self.app.push_screen(SourcesScreen(
                self.anime, resume_episode=self.resume_episode, audio=Audio.SUB,
            ))
            return
        if isinstance(item, SourceItem):
            self.app.pop_screen()
            self.app.push_screen(
                DetailScreen(
                    self.anime,
                    resume_episode=self.resume_episode,
                    source=item.source,
                    audio=item.source.audio,
                )
            )
