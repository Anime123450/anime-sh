"""Detail screen: anime metadata + episode list; Enter plays an episode.

Episodes are listed from AniList's episode count — instant, no provider touched.
Providers are consulted only when the user actually plays an episode, which is
what keeps browsing fast even when scrapers are slow or down.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, ListView, Static

from ...domain.errors import NoStreamsFound
from ...domain.models import Anime, Audio
from ..coverart import fetch_cover, render_cover
from ..format import meta_line, next_episode_line
from ..widgets import EpisodeItem

_COVER_COLS = 28


class DetailScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("n", "next_season", "Next season"),
    ]

    DEFAULT_CSS = """
    DetailScreen #detail-top { height: auto; }
    DetailScreen #detail-cover { width: 30; height: auto; padding: 0 1; }
    DetailScreen #detail-meta { width: 1fr; height: auto; }
    """

    def __init__(self, anime: Anime, *, resume_episode: float | None = None,
                 source=None) -> None:
        super().__init__()
        self.anime = anime
        self.resume_episode = resume_episode
        self.source = source  # a chosen SourceOption, or None to fan out

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            with Horizontal(id="detail-top"):
                yield Static("", id="detail-cover")
                yield Static(self._header_text(), id="detail-meta")
            yield ListView(id="episodes")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self.anime.title.preferred
        if self.anime.episode_count:
            self.sub_title = f"{self.anime.episode_count} episodes planned"
        self._load_cover()
        self._populate_episodes()

    @work(exclusive=True, group="cover")
    async def _load_cover(self) -> None:
        # Cover art is pure decoration: fetch + render off the main path, and
        # never surface a failure (no Pillow, offline, odd image → just skip).
        url = self.anime.cover_url
        if not url:
            return
        data = await fetch_cover(url)
        if not data:
            return
        art = render_cover(data, cols=_COVER_COLS)
        if art is not None:
            try:
                self.query_one("#detail-cover", Static).update(art)
            except Exception:
                pass

    @work(exclusive=True, group="episodes")
    async def _populate_episodes(self) -> None:
        # Show AniList's count immediately (instant), then refine to what the
        # providers actually have — for an airing show that's usually fewer.
        await self._load_marks()
        count = self.anime.episode_count or 0
        await self._render_episodes([float(n) for n in range(1, count + 1)] or [1.0])
        self._refine_episodes()

    async def _load_marks(self) -> None:
        """Watched (✓) and in-progress (▸ N%) marks from the library."""
        self._watched: set[float] = set()
        self._partial: dict[float, int] = {}
        try:
            for p in await self.app.services.library.progress_for(self.anime.id):
                if p.completed:
                    self._watched.add(p.episode)
                elif p.position_s > 0:
                    self._partial[p.episode] = round(p.fraction * 100)
        except Exception:
            pass  # marks are decoration; never block the episode list

    @work(exclusive=True, group="episodes-refine")
    async def _refine_episodes(self) -> None:
        try:
            available = await self.app.services.playback.available_episodes(
                self.anime, source=self.source
            )
        except Exception:
            return
        if available:
            src = f" · {self.source.provider}" if self.source else ""
            planned = self.anime.episode_count
            of = f"/{planned}" if planned else ""
            self.sub_title = f"{len(available)}{of} episodes available{src}"
            # Keep the whole planned season visible; mark what isn't out yet
            # instead of hiding it.
            numbers = sorted(
                {float(n) for n in range(1, (planned or 0) + 1)} | set(available)
            )
            await self._render_episodes(numbers, available=set(available))

    async def _render_episodes(
        self, numbers: list[float], available: set[float] | None = None
    ) -> None:
        lv = self.query_one("#episodes", ListView)
        await lv.clear()
        watched = getattr(self, "_watched", set())
        partial = getattr(self, "_partial", {})
        select_index = 0
        for i, number in enumerate(numbers):
            is_resume = self.resume_episode is not None and number == self.resume_episode
            if is_resume:
                select_index = i
            lv.append(
                EpisodeItem(
                    number,
                    watched=number in watched,
                    resume_s=1 if is_resume else 0,
                    progress_pct=partial.get(number),
                    available=available is None or number in available,
                )
            )
        if self.resume_episode is None and (watched or partial):
            # Land the cursor on the next thing to watch: an in-progress
            # episode first, else the first unwatched available one.
            select_index = next(
                (i for i, n in enumerate(numbers) if n in partial),
                next(
                    (i for i, n in enumerate(numbers)
                     if n not in watched and (available is None or n in available)),
                    select_index,
                ),
            )
        lv.index = select_index
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, EpisodeItem):
            if not item.available:
                self.notify(
                    f"Episode {item.number:g} isn't available yet.",
                    severity="warning",
                )
                return
            self._play(item.number)

    @work(exclusive=True, group="play")
    async def _play(self, number: float) -> None:
        total = self.anime.episode_count
        label = f"{number:g}/{total}" if total else f"{number:g}"
        self.notify(f"Resolving Episode {label}…", timeout=4)
        try:
            await self.app.services.playback.play_and_track(
                self.anime, number, audio=Audio.SUB, source=self.source
            )
        except NoStreamsFound:
            self.notify(
                f"No provider had {self.anime.title.preferred} Episode {number:g}.",
                severity="error",
            )
        except Exception as e:  # keep the TUI alive on any playback failure
            self.notify(f"Playback error: {e}", severity="error")

    def action_next_season(self) -> None:
        self._open_next_season()

    @work(exclusive=True, group="sequel")
    async def _open_next_season(self) -> None:
        try:
            sequel = await self.app.services.metadata.sequel(self.anime.id)
        except Exception:
            sequel = None
        if sequel is None:
            self.notify("No next season found.", severity="warning")
            return
        from .sources import SourcesScreen

        self.notify(f"Next season: {sequel.title.preferred}")
        self.app.push_screen(SourcesScreen(sequel))

    def _header_text(self) -> str:
        a = self.anime
        lines = [f"[b]{a.title.preferred}[/b]", f"[dim]{meta_line(a)}[/dim]"]
        nxt = next_episode_line(a)
        if nxt:
            lines.append(f"[green]▸ Next: {nxt}[/green]")
        if a.genres:
            lines.append(f"[cyan]{', '.join(a.genres[:5])}[/cyan]")
        synopsis = (a.synopsis or "").strip()
        if len(synopsis) > 400:
            synopsis = synopsis[:400].rsplit(" ", 1)[0] + "…"
        if synopsis:
            lines.append("")
            lines.append(synopsis)
        return "\n".join(lines)
