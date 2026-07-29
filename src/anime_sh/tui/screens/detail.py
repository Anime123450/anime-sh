"""Detail screen: anime metadata + episode list; Enter plays an episode.

Episodes are listed from AniList's episode count — instant, no provider touched.
Providers are consulted only when the user actually plays an episode, which is
what keeps browsing fast even when scrapers are slow or down.

The show is re-fetched fresh from the metadata source on open, so a card reached
from Continue Watching or favorites (whose cached row lacks the synopsis, airing
schedule, studio and score) renders just as fully as one reached from search.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, ListView, Static

from ...domain.errors import NoStreamsFound
from ...domain.models import Anime, Audio
from ..coverart import (
    fetch_cover,
    graphics_cover_widget,
    graphics_protocol_active,
    render_cover,
)
from ..format import (
    episode_air_label,
    meta_line,
    next_episode_line,
    watch_summary,
)
from ..widgets import EpisodeItem

# Cover width in character cells. Kept modest so the poster is a tasteful accent
# beside the metadata, not a wall of pixels — and smaller means denser, so it
# also reads a touch sharper.
_COVER_COLS = 32


class DetailScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("n", "next_season", "Next season"),
    ]

    DEFAULT_CSS = """
    DetailScreen #detail-top { height: auto; }
    DetailScreen #detail-cover { width: 34; height: auto; padding: 0 2 0 0; }
    DetailScreen #detail-meta { width: 1fr; height: auto; }
    DetailScreen #detail-progress { height: auto; padding: 1 0 0 0; }
    DetailScreen #detail-action { height: auto; padding: 0 0 1 0; }
    """

    def __init__(self, anime: Anime, *, resume_episode: float | None = None,
                 source=None) -> None:
        super().__init__()
        self.anime = anime
        self.resume_episode = resume_episode
        self.source = source  # a chosen SourceOption, or None to fan out
        self._numbers: list[float] = []
        self._available: set[float] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            with Horizontal(id="detail-top"):
                yield Container(id="detail-cover")
                yield Static(self._header_text(), id="detail-meta")
            yield Static("", id="detail-progress")
            yield Static("", id="detail-action")
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
        self._cover_data = data
        await self._mount_cover()

    async def _mount_cover(self) -> None:
        data = getattr(self, "_cover_data", None)
        if not data:
            return
        try:
            container = self.query_one("#detail-cover", Container)
        except Exception:
            return
        await container.remove_children()
        # Prefer a true-bitmap render (Sixel/kitty) when the terminal supports
        # it — crisp, unlike the unicode-block fallback. Any failure drops back
        # to the block render so the cover still shows.
        if graphics_protocol_active():
            widget = graphics_cover_widget(data, _COVER_COLS)
            if widget is not None:
                try:
                    await container.mount(widget)
                    return
                except Exception:
                    pass
        art = render_cover(data, cols=_COVER_COLS)
        if art is not None:
            try:
                await container.mount(Static(art))
            except Exception:
                pass

    def on_resize(self, event) -> None:
        # A Sixel image doesn't reflow when the terminal is resized (e.g.
        # maximised) — the old pixels linger and the layout smears. Re-mount the
        # cover so it redraws cleanly at the new size, and force a full repaint
        # to clear any stale graphics.
        if getattr(self, "_cover_data", None):
            self._remount_cover()

    @work(exclusive=True, group="cover")
    async def _remount_cover(self) -> None:
        await self._mount_cover()
        try:
            self.app.refresh(repaint=True)
        except Exception:
            pass

    @work(exclusive=True, group="episodes")
    async def _populate_episodes(self) -> None:
        # One serialized pass so the three sources never race each other:
        #   1. instant render from AniList's planned count (+ watched marks)
        #   2. enrich to full, fresh metadata (synopsis / airing / studio / score)
        #   3. refine to what the provider actually has, with air countdowns
        await self._load_marks()
        count = self.anime.episode_count or 0
        await self._render_episodes([float(n) for n in range(1, count + 1)] or [1.0])

        fresh = await self._fresh_metadata()
        if fresh is not None:
            self.anime = fresh
            self._refresh_header()
            self._refresh_progress()  # fresh metadata may fill in the total

        available = await self._available_episodes()
        planned = self.anime.episode_count or 0
        numbers = sorted({float(n) for n in range(1, planned + 1)} | set(available or []))
        await self._render_episodes(
            numbers or [1.0],
            available=set(available) if available else None,
        )
        self._refresh_subtitle(available)

    async def _fresh_metadata(self) -> Anime | None:
        """Re-fetch the show from the metadata source (cached ~1h) so every
        card renders full detail, not just what a cached row happened to keep.
        Best-effort: None if the source can't be reached or lacks ``get``."""
        get = getattr(self.app.services.metadata, "get", None)
        if get is None or self.anime.id.anilist is None:
            return None
        try:
            return await get(self.anime.id)
        except Exception:
            return None

    async def _available_episodes(self) -> list[float] | None:
        try:
            return await self.app.services.playback.available_episodes(
                self.anime, source=self.source
            )
        except Exception:
            return None

    async def _load_marks(self) -> None:
        """Compute the watch state from the library.

        Watching is linear, so the *furthest completed episode* implies every
        earlier one is watched too — that's what makes a show whose progress was
        synced from AniList (one "watched up to N" row) light up episodes 1..N,
        not just N. In-progress percents past that mark are kept; ones at or
        below it are stale (superseded by a later completion) and dropped."""
        self._watched_through: float = 0
        self._partial: dict[float, int] = {}
        try:
            for p in await self.app.services.library.progress_for(self.anime.id):
                if p.completed:
                    self._watched_through = max(self._watched_through, p.episode)
                elif p.position_s > 0 and p.duration_s > 0:
                    self._partial[p.episode] = round(p.fraction * 100)
        except Exception:
            pass  # marks are decoration; never block the episode list
        self._partial = {
            e: pct for e, pct in self._partial.items() if e > self._watched_through
        }
        self._refresh_progress()

    def _refresh_progress(self) -> None:
        """Update the overall watch-progress bar under the header."""
        try:
            self.query_one("#detail-progress", Static).update(
                watch_summary(int(getattr(self, "_watched_through", 0)),
                              self.anime.episode_count,
                              ep_minutes=self.anime.duration_min)
            )
        except Exception:
            pass

    async def _render_episodes(
        self, numbers: list[float], available: set[float] | None = None
    ) -> None:
        self._numbers = numbers
        self._available = available
        lv = self.query_one("#episodes", ListView)
        await lv.clear()
        watched_through = getattr(self, "_watched_through", 0)
        partial = getattr(self, "_partial", {})

        def is_avail(n: float) -> bool:
            return available is None or n in available

        # Next to watch: an explicit resume target, else the in-progress
        # episode, else the first available one past the watched-through mark.
        next_number = (
            self.resume_episode
            if self.resume_episode is not None
            else min(partial) if partial
            else next((n for n in numbers if n > watched_through and is_avail(n)), None)
        )
        select_index = 0
        for i, number in enumerate(numbers):
            avail = is_avail(number)
            is_watched = number <= watched_through
            pct = partial.get(number)
            if number == next_number:
                select_index = i
            lv.append(
                EpisodeItem(
                    number,
                    watched=is_watched,
                    resume_s=1 if number == self.resume_episode else 0,
                    progress_pct=pct,
                    available=avail,
                    air_label=None if avail else episode_air_label(self.anime, number),
                    is_next=(not is_watched and pct is None
                             and number == next_number and avail),
                )
            )
        lv.index = select_index
        lv.focus()
        self._refresh_action(next_number, partial, watched_through)

    def _refresh_action(self, next_number, partial: dict, watched_through) -> None:
        """The one-line call-to-action above the episode list: what pressing
        Enter will do — resume, start, play next, or a done note."""
        total = self.anime.episode_count
        if next_number is None:
            text = ("[green]✓ You've finished this series.[/green]"
                    if total and watched_through >= total else "")
        else:
            n = f"{next_number:g}"
            pct = partial.get(next_number)
            if pct:
                text = (f"[b cyan]▶ Resume Episode {n}[/b cyan] "
                        f"[dim]· {pct}% watched — press Enter[/dim]")
            elif watched_through <= 0:
                text = f"[b cyan]▶ Start Episode {n}[/b cyan] [dim]— press Enter[/dim]"
            else:
                text = (f"[b cyan]▶ Play Episode {n}[/b cyan] "
                        f"[dim]· up next — press Enter[/dim]")
        try:
            self.query_one("#detail-action", Static).update(text)
        except Exception:
            pass

    def _refresh_subtitle(self, available: list[float] | None) -> None:
        if available:
            src = f" · {self.source.provider}" if self.source else ""
            planned = self.anime.episode_count
            of = f"/{planned}" if planned else ""
            self.sub_title = f"{len(available)}{of} episodes available{src}"

    def _refresh_header(self) -> None:
        try:
            self.query_one("#detail-meta", Static).update(self._header_text())
        except Exception:
            pass
        self.title = self.anime.title.preferred

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
        except NoStreamsFound as e:
            # The service distinguishes "this source doesn't list the episode"
            # from "it listed it but nothing would play" — surface that instead
            # of flattening both into a generic miss, which hides the real cause.
            hint = " Try another source (Esc, then pick a different one)." if self.source else ""
            self.notify(f"Episode {number:g}: {e}{hint}", severity="error")
        except Exception as e:  # keep the TUI alive on any playback failure
            self.notify(f"Playback error: {e}", severity="error")
        # Playback (and any auto-next) is done and progress is saved — refresh
        # the ✓ / ▸ marks in place so the list reflects what you just watched
        # without needing to leave and re-open the screen.
        await self._load_marks()
        if self._numbers:
            await self._render_episodes(self._numbers, available=self._available)

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
        lines = [f"[b]{a.title.preferred}[/b]"]
        # Show the alternate title when English and romaji differ — helps you
        # confirm it's the right show.
        alt = a.title.romaji if a.title.english and a.title.romaji else None
        if alt and alt != a.title.preferred:
            lines.append(f"[dim italic]{alt}[/dim italic]")
        lines.append(f"[dim]{meta_line(a)}[/dim]")
        nxt = next_episode_line(a)
        if nxt:
            lines.append(f"[green]▸ Next: {nxt}[/green]")
        if a.genres:
            lines.append(f"[cyan]{' · '.join(a.genres[:5])}[/cyan]")
        synopsis = (a.synopsis or "").strip()
        if synopsis:
            if len(synopsis) > 500:
                synopsis = synopsis[:500].rsplit(" ", 1)[0] + "…"
            lines.append("")
            lines.append(synopsis)
        return "\n".join(lines)
