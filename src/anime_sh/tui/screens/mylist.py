"""My List screen: the user's AniList list, grouped by status.

Fetched live from the tracker on mount. Selecting an entry goes through the same
source picker as everywhere else, so playing from your list is one keystroke.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, ListView, LoadingIndicator

from ..rows import GAP, Row, columns_for, title_target
from ..shell import ActionBar, TopBar, spaced
from ..widgets import AnimeItem
from .sources import SourcesScreen

# Friendly status → display order + label.
_ORDER = ["CURRENT", "REPEATING", "PAUSED", "PLANNING", "COMPLETED", "DROPPED"]
_LABEL = {
    "CURRENT": "Watching", "REPEATING": "Rewatching", "PAUSED": "Paused",
    "PLANNING": "Planning", "COMPLETED": "Completed", "DROPPED": "Dropped",
}


class _Group(ListView):
    """One status group. Up and down run off its ends into the next group.

    Each group was its own ten-row box with its own scrollbar, and the cursor
    stopped dead at the bottom of one: reaching Planning meant knowing to press
    Tab. Now the groups grow to their full length, the page is the only thing
    that scrolls, and the arrows walk straight through.
    """

    def action_cursor_down(self) -> None:
        if self.index is not None and self.index >= len(self.children) - 1:
            self.screen.step_group(self, +1)
            return
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        if not self.index:
            self.screen.step_group(self, -1)
            return
        super().action_cursor_up()


class MyListScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    MyListScreen #mylist-body { padding: 0 2; }
    """

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with VerticalScroll(id="mylist-body"):
            yield LoadingIndicator(id="mylist-loading")
        yield ActionBar(id="actionbar")

    def on_mount(self) -> None:
        self.title = "My List"
        with contextlib.suppress(Exception):
            width = self.size.width or 100
            # The screen's one heading was a Label reading "My List" directly
            # under a header that also read "My List". The bar says it now, and
            # the status groups below are the headings worth having.
            self.query_one("#topbar", TopBar).render_bar(
                "My List", width, hint="from AniList")
            self.query_one("#actionbar", ActionBar).render_actions(
                width, zoom=False, contextual=(("↵", "play"), ("esc", "back")))
        self._load()

    @work(exclusive=True, group="mylist")
    async def _load(self) -> None:
        try:
            entries = await self.app.services.tracker.fetch_list()
        except Exception as e:
            self.notify(f"Couldn't load your list: {e}", severity="error")
            entries = []
        try:
            self.query_one("#mylist-loading").remove()
        except Exception:
            pass
        body = self.query_one("#mylist-body", VerticalScroll)
        if not entries:
            await body.mount(Label("[dim]Your list is empty.[/dim]"))
            return
        groups: dict[str, list] = {}
        for e in entries:
            groups.setdefault(e.status, []).append(e)
        order = [s for s in _ORDER if s in groups] + [
            s for s in groups if s not in _ORDER
        ]
        for status in order:
            rows = groups[status]
            await body.mount(
                Label(
                    f"{spaced(_LABEL.get(status, status.title()))}  {len(rows)}",
                    # `shelf-label`, not the renamed-away `section`: these came
                    # out as unstyled body text, indistinguishable from the rows
                    # they were meant to be grouping.
                    classes="shelf-label",
                )
            )
            lv = _Group()
            await body.mount(lv)
            built = []
            for e in rows:
                total = e.anime.episode_count
                prog = f"{e.progress}/{total}" if total else f"{e.progress}"
                built.append((e, Row(
                    title=e.anime.title.preferred,
                    position=prog,
                    status=f"[$warning]★ {e.score:g}[/]" if e.score else "",
                    status_cells=len(f"★ {e.score:g}") if e.score else 0,
                )))
            # Sized to the titles this group actually holds, like every other
            # list on the home screen. Without it a group of short names reserved
            # the whole measure and left the score column marooned to the right.
            target = title_target([r for _, r in built])
            cols = columns_for(self.size.width or 100, target)
            if cols.status and not any(e.score for e, _ in built):
                # Nothing in this group is scored, so the score column was 16
                # empty cells while titles were cut short beside it. Give them
                # the room.
                cols = replace(cols, status=0, title=max(
                    cols.title, min(cols.title + GAP + cols.status, target or 0)))
            for e, row in built:
                lv.append(AnimeItem(
                    e.anime,
                    row,
                    cols,
                ))

        # The page used to open with focus on the scroll container, so Enter
        # did nothing until Tab found a list.
        with contextlib.suppress(Exception):
            first = self.query(_Group).first()
            first.index = 0
            first.focus()

    def step_group(self, group: _Group, step: int) -> None:
        groups = list(self.query(_Group))
        i = groups.index(group) + step
        if not 0 <= i < len(groups):
            if step < 0:
                # Up from the very first row: show the heading above it too.
                self.query_one("#mylist-body", VerticalScroll).scroll_home(animate=False)
            return
        nxt = groups[i]
        nxt.index = 0 if step > 0 else len(nxt.children) - 1
        nxt.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, AnimeItem):
            self.app.push_screen(SourcesScreen(item.anime))
