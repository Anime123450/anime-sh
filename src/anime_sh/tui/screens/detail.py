"""Detail screen: anime metadata + episode list; Enter plays an episode.

Episodes are listed from AniList's episode count — instant, no provider touched.
Providers are consulted only when the user actually plays an episode, which is
what keeps browsing fast even when scrapers are slow or down.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, ListView, Static

from ...domain.errors import NoStreamsFound
from ...domain.models import Anime, Audio
from ..widgets import EpisodeItem


class DetailScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, anime: Anime, *, resume_episode: float | None = None) -> None:
        super().__init__()
        self.anime = anime
        self.resume_episode = resume_episode

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(self._header_text(), id="detail-meta")
            yield ListView(id="episodes")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self.anime.title.preferred
        self._populate_episodes()

    @work(exclusive=True, group="episodes")
    async def _populate_episodes(self) -> None:
        lv = self.query_one("#episodes", ListView)
        await lv.clear()
        count = self.anime.episode_count or 0
        numbers = [float(n) for n in range(1, count + 1)] or [1.0]
        select_index = 0
        for i, number in enumerate(numbers):
            is_resume = self.resume_episode is not None and number == self.resume_episode
            if is_resume:
                select_index = i
            # The playback service reads the exact resume position itself; here
            # we only flag which episode to jump back into.
            lv.append(EpisodeItem(number, resume_s=1 if is_resume else 0))
        lv.index = select_index
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, EpisodeItem):
            self._play(item.number)

    @work(exclusive=True, group="play")
    async def _play(self, number: float) -> None:
        self.notify(f"Resolving Episode {number:g}…", timeout=4)
        try:
            await self.app.services.playback.play_and_track(
                self.anime, number, audio=Audio.SUB
            )
        except NoStreamsFound:
            self.notify(
                f"No provider had {self.anime.title.preferred} Episode {number:g}.",
                severity="error",
            )
        except Exception as e:  # keep the TUI alive on any playback failure
            self.notify(f"Playback error: {e}", severity="error")

    def _header_text(self) -> str:
        a = self.anime
        bits = [x for x in (a.format.value, a.status.value.replace("_", " ").title(),
                            f"{a.episode_count} eps" if a.episode_count else None,
                            str(a.year) if a.year else None) if x]
        line = "  ·  ".join(bits)
        genres = ", ".join(a.genres[:5])
        synopsis = (a.synopsis or "").strip()
        if len(synopsis) > 400:
            synopsis = synopsis[:400].rsplit(" ", 1)[0] + "…"
        return f"[b]{a.title.preferred}[/b]\n[dim]{line}[/dim]\n[cyan]{genres}[/cyan]\n\n{synopsis}"
