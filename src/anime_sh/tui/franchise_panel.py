"""Franchise panel for the detail screen.

Shows every entry in the franchise in watch order (release date),
with the user's watch status (✓ completed / ◐ in progress / ○ not started),
clear distinguishing titles, format, year, episode counts, and progress.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ListItem, ListView, Static

from ..app.franchise import EntryStatus, Franchise, FranchiseEntry
from ..domain.models import Anime
from .format import shelf_titles

if TYPE_CHECKING:
    pass


class FranchiseItem(ListItem):
    """A single row in the franchise watch order list."""

    DEFAULT_CSS = """
    FranchiseItem {
        height: 1;
        padding: 0 1;
        margin: 0;
        background: transparent;
    }
    FranchiseItem:hover {
        background: $surface;
    }
    FranchiseItem.-current {
        background: $surface-lighten-1;
    }
    FranchiseItem.-subdued {
        color: $text-muted;
    }
    /* One row per entry: the title gives way (ellipsis), never the
       format/year/eps/progress on the right, which used to wrap alone. */
    FranchiseItem > Horizontal {
        height: 1;
        width: 100%;
    }
    FranchiseItem .fi-main {
        width: 1fr;
        height: 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    FranchiseItem .fi-side {
        width: auto;
        height: 1;
        padding: 0 0 0 2;
        text-wrap: nowrap;
    }
    """

    def __init__(self, entry: FranchiseEntry, *, title: str, is_suggested: bool = False) -> None:
        super().__init__()
        self.entry = entry
        self.entry_title = title
        self.is_suggested = is_suggested
        if entry.is_current:
            self.add_class("-current")
        if not entry.main_line:
            self.add_class("-subdued")

    def compose(self) -> ComposeResult:
        main, side = self._parts()
        with Horizontal():
            yield Static(main, markup=True, classes="fi-main")
            yield Static(side, markup=True, classes="fi-side")

    def _format_text(self) -> str:
        main, side = self._parts()
        return f"{main}  {side}" if side else main

    def _parts(self) -> tuple[str, str]:
        """(glyph · kind · title · up-next, format · year · eps · progress)."""
        e = self.entry
        # 1. Status glyph
        if e.status == EntryStatus.COMPLETED:
            glyph = "[b $success]✓[/]"
        elif e.status == EntryStatus.IN_PROGRESS:
            glyph = "[b $warning]◐[/]"
        else:
            glyph = "[dim]○[/dim]"

        # 2. Kind label
        kind = e.kind.lower()
        if kind == "season":
            kind_lbl = "[b $primary]season[/]"
        elif kind in ("movie", "ova", "special"):
            kind_lbl = f"[$accent]{kind}[/]"
        elif not e.main_line:
            kind_lbl = f"[dim]{kind}[/dim]"
        else:
            kind_lbl = f"[$secondary]{kind}[/]"

        # 3. Indent side stories / spin-offs
        indent = "  " if not e.main_line else ""

        # 4. Title
        if e.is_current:
            title_str = f"[b $primary]{self.entry_title}[/] [dim $primary](current)[/]"
        elif not e.main_line:
            title_str = f"[dim]{self.entry_title}[/dim]"
        else:
            title_str = self.entry_title

        # 5. Badges: format · year · eps
        parts = []
        if e.anime.format:
            parts.append(e.anime.format.name.replace("_", " "))
        if e.anime.year:
            parts.append(str(e.anime.year))
        elif e.anime.start_date:
            parts.append(str(e.anime.start_date.year))
        if e.total:
            parts.append(f"{e.total} eps")
        meta_str = f"[dim]{' · '.join(parts)}[/dim]" if parts else ""

        # 6. Progress
        if e.total:
            prog_str = f"[dim]{e.watched}/{e.total}[/dim]"
        elif e.watched:
            prog_str = f"[dim]{e.watched} watched[/dim]"
        else:
            prog_str = ""

        # 7. Suggested indicator
        sugg_str = " [b $accent]→ up next[/] " if self.is_suggested and not e.is_current else ""

        # "→ up next" rides in the side column: in the title column a long
        # title's ellipsis would cut it off, and it's the cue that matters.
        main = f"{indent}{glyph} {kind_lbl} {title_str}"
        side = "  ".join(x for x in (sugg_str.strip(), meta_str, prog_str) if x)
        return main, side


class FranchisePanel(Vertical):
    """Panel displaying the entire franchise graph in release/watch order."""

    DEFAULT_CSS = """
    FranchisePanel {
        width: 100%;
        height: 1fr;
        min-height: 8;
        background: $surface;
        padding: 0 1;
        margin: 1 0 0 0;
        border-top: solid $border;
    }
    FranchisePanel #franchise-title {
        height: 1;
        width: 100%;
        padding: 0 1;
        margin: 0;
        color: $text;
        text-style: bold;
        background: $surface;
    }
    FranchisePanel #franchise-list {
        width: 100%;
        height: 1fr;
        background: transparent;
        padding: 0;
        margin: 0;
    }
    """

    def __init__(self, franchise: Franchise | None = None, *, id: str | None = None, **kwargs) -> None:
        super().__init__(id=id or "detail-franchise", **kwargs)
        self.franchise: Franchise | None = franchise

    def compose(self) -> ComposeResult:
        count = len(self.franchise.entries) if self.franchise else 0
        title = f"Franchise · {count} entries" if count else "Franchise"
        yield Static(f"[b $primary]◆ {title}[/]  [dim]↵ open · w mark watched elsewhere · tab switch[/dim]",
                     id="franchise-title", markup=True)
        yield ListView(id="franchise-list")

    def set_franchise(self, franchise: Franchise) -> None:
        self.franchise = franchise
        count = len(franchise.entries)
        try:
            title_w = self.query_one("#franchise-title", Static)
            title_w.update(f"[b $primary]◆ Franchise · {count} entries[/]  [dim]↵ open · w mark watched elsewhere · tab switch[/dim]")
        except Exception:
            pass

        lv = self.query_one("#franchise-list", ListView)
        lv.clear()
        if not franchise.entries:
            return

        # Generate disambiguated titles
        raw_titles = [e.anime.title.preferred for e in franchise.entries]
        try:
            titles = shelf_titles(raw_titles, width=40)
        except Exception:
            titles = raw_titles

        initial_index = 0
        for i, entry in enumerate(franchise.entries):
            t = titles[i] if i < len(titles) else entry.anime.title.preferred
            is_sugg = (franchise.suggested_index == i)
            item = FranchiseItem(entry, title=t, is_suggested=is_sugg)
            lv.append(item)
            if entry.is_current:
                initial_index = i

        if lv.children:
            lv.index = initial_index
