"""A cautious guide to direct AniList prequel/sequel and franchise links."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, LoadingIndicator, Static

from ...domain.models import Anime
from ..shell import ActionBar, TopBar, spaced


def _year_key(anime: Anime) -> tuple[int, str]:
    return (anime.year if anime.year is not None else 9999,
            anime.title.preferred.casefold())


def build_watch_order(
    current: Anime, relations: list[tuple[str, Anime]],
) -> list[tuple[str, Anime]]:
    """Place explicit prequels before and sequels after the selected entry.

    Other relation types are listed separately: release year alone cannot
    establish where a movie, side story, recap, or spin-off belongs.
    """
    before: list[Anime] = []
    after: list[Anime] = []
    related: list[tuple[str, Anime]] = []
    seen = {current.id.key}
    for relation, anime in relations:
        if anime.id.key in seen:
            continue
        seen.add(anime.id.key)
        kind = relation.upper()
        if kind == "PREQUEL":
            before.append(anime)
        elif kind == "SEQUEL":
            after.append(anime)
        else:
            label = kind.replace("_", " ").lower() or "related"
            related.append((f"Related · {label}", anime))

    rows = [("Before this", anime) for anime in sorted(before, key=_year_key)]
    rows.append(("Current", current))
    rows.extend(("After this", anime) for anime in sorted(after, key=_year_key))
    rows.extend((label, anime) for label, anime in sorted(
        related, key=lambda item: (_year_key(item[1]), item[0])
    ))
    return rows


class WatchOrderItem(ListItem):
    def __init__(self, relation: str, anime: Anime) -> None:
        self.anime = anime
        self.relation = relation
        facts = [anime.format.value]
        if anime.year:
            facts.append(str(anime.year))
        if anime.episode_count:
            facts.append(f"{anime.episode_count} eps")
        detail = " · ".join(facts)
        super().__init__(Static(
            f"{relation} — {anime.title.preferred}\n  {detail}",
            markup=False,
        ))


class WatchOrderScreen(Screen):
    """Direct relations: an orientation aid, not a canon/timing guarantee."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]

    DEFAULT_CSS = """
    WatchOrderScreen #watch-order-body {
        padding: 0 2;
        align: center top;
    }
    WatchOrderScreen #watch-order-note {
        color: $text-muted;
        margin: 0 0 1 0;
        width: 1fr;
        max-width: 110;
    }
    WatchOrderScreen #watch-order-status {
        color: $text-muted;
        padding: 1 0;
        width: 1fr;
        max-width: 110;
    }
    WatchOrderScreen #watch-order-list {
        width: 1fr;
        max-width: 110;
    }
    """

    def __init__(self, anime: Anime) -> None:
        super().__init__()
        self.anime = anime

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with VerticalScroll(id="watch-order-body"):
            yield Label(spaced("Seasons & related"), classes="shelf-label")
            yield Static(
                "Direct AniList links only. Prequels/sequels are ordered around "
                "this entry; related movies and side stories are not assigned "
                "a watch position.",
                markup=False,
                id="watch-order-note",
            )
            yield LoadingIndicator(id="watch-order-loading")
            yield Static("Loading related entries…", id="watch-order-status", markup=False)
            yield ListView(id="watch-order-list")
        yield ActionBar(id="actionbar")

    def on_mount(self) -> None:
        self.title = f"Related to {self.anime.title.preferred}"
        width = self.size.width or 100
        self.query_one("#topbar", TopBar).render_bar(
            self.title, width, hint="seasons & related")
        self.query_one("#actionbar", ActionBar).render_actions(
            width, zoom=False, contextual=(("↵", "open"), ("esc", "back")))
        self._load_relations()

    @work(exclusive=True, group="watch-order")
    async def _load_relations(self) -> None:
        status = self.query_one("#watch-order-status", Static)
        try:
            relations = await self.app.services.metadata.relations(self.anime.id)
            rows = build_watch_order(self.anime, relations)
        except Exception as exc:
            self.query_one("#watch-order-loading").display = False
            status.update(f"Couldn't load related titles: {exc}")
            return

        self.query_one("#watch-order-loading").display = False
        lv = self.query_one("#watch-order-list", ListView)
        lv.display = True
        if not relations:
            status.update("AniList lists no directly related anime for this title.")
            return
        status.display = False
        for label, anime in rows:
            lv.append(WatchOrderItem(label, anime))
        lv.index = 0
        lv.focus(scroll_visible=False)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, WatchOrderItem):
            return
        from .detail import DetailScreen

        self.app.push_screen(DetailScreen(event.item.anime))
