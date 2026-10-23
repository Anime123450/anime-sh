"""A show's whole franchise: every season, movie, OVA and special, in watch
order, each with the user's own progress.

Nothing here assumes you start at season one. You may have watched season 1
somewhere else and want the movie that sits between seasons 2 and 3. So the
franchise is a map you can enter anywhere. It shows where you actually are
(from your progress, wherever it came from) and suggests the next thing from
there, not from the beginning.

CONTRACT: the TUI codes against the names and fields in this module. Keep
them stable; add, don't rename.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

from ..domain.errors import AnimeShError
from ..domain.models import Anime, Format

log = logging.getLogger(__name__)


class EntryStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class FranchiseEntry:
    anime: Anime
    #: How this entry relates to the franchise, for display, in lowercase:
    #: "season", "movie", "ova", "special", "side story", "spin-off",
    #: "summary", "alternative", ...
    kind: str
    #: True for the main story line: the TV/ONA seasons and movies on the
    #: prequel/sequel chain. Side stories, recaps and spin-offs are False.
    main_line: bool
    status: EntryStatus
    watched: int          # completed episodes recorded for this entry
    total: int | None     # episode count, None while unknown/airing
    is_current: bool      # the entry whose detail page asked for this


@dataclass(frozen=True, slots=True)
class Franchise:
    #: Watch order: by start date (release order), main line and side stories
    #: interleaved where they actually came out. Recaps last within their year.
    entries: tuple[FranchiseEntry, ...] = ()
    current_index: int = 0
    #: The entry to watch next, judged from the user's progress across the
    #: whole franchise rather than from season one. None when everything is
    #: done (or nothing is known).
    suggested_index: int | None = None
    #: True when the relation walk hit its cap, so the list may be partial.
    truncated: bool = False

    @property
    def current(self) -> FranchiseEntry | None:
        return self.entries[self.current_index] if self.entries else None

    @property
    def suggested(self) -> FranchiseEntry | None:
        if self.suggested_index is None:
            return None
        return self.entries[self.suggested_index]


@dataclass(frozen=True, slots=True)
class EpisodeInfo:
    """Per-episode metadata for display. Every field is optional: many shows
    have none of it, and the detail screen must look right without it."""
    number: int
    title: str | None = None
    aired: str | None = None      # ISO date, "2016-04-04"
    filler: bool = False
    recap: bool = False


# --------------------------------------------------------------------------- #
# Walk configuration
# --------------------------------------------------------------------------- #
MAX_ENTRIES = 30
MAX_RELATION_FETCHES = 12
FETCH_CONCURRENCY = 4
JIKAN_MAX_PAGES = 4

#: Relations the walk follows *through* (the neighbour is fetched in turn).
_WALK = frozenset({"PREQUEL", "SEQUEL", "PARENT", "SIDE_STORY"})
#: Relations that put an entry on the map. SUMMARY and SPIN_OFF are shown but
#: never expanded: a spin-off's own franchise is not this franchise. Everything
#: else (ALTERNATIVE, CHARACTER, OTHER, ADAPTATION, SOURCE) is not shown.
_KEEP = _WALK | {"SUMMARY", "SPIN_OFF"}
_CHAIN = frozenset({"PREQUEL", "SEQUEL"})
_MAIN_FORMATS = frozenset({Format.TV, Format.TV_SHORT, Format.ONA, Format.MOVIE})
_SERIES_FORMATS = frozenset({Format.TV, Format.TV_SHORT, Format.ONA})

_REL_LABEL = {
    "SIDE_STORY": "side story",
    "SPIN_OFF": "spin-off",
    "SUMMARY": "summary",
    "PARENT": "parent story",
}


@dataclass(slots=True)
class _Node:
    anime: Anime
    rel: str | None           # how the walk first reached it; None for the root
    expand: bool = True


def _sort_key(node: _Node) -> tuple:
    a = node.anime
    d = a.start_date
    if d is not None:
        year, when = d.year, d
    elif a.year:
        year, when = a.year, date(a.year, 1, 1)
    else:
        return (1, 0, 0, date.max, a.title.preferred.lower())
    # Recaps come last within their year: a summary airs alongside the season
    # it recaps and should not be mistaken for the next chapter.
    return (0, year, node.rel == "SUMMARY", when, a.title.preferred.lower())


def _kind(node: _Node, main_line: bool) -> str:
    fmt = node.anime.format
    if main_line:
        return "movie" if fmt is Format.MOVIE else "season"
    if node.rel == "SUMMARY":
        return "summary"
    if fmt is Format.MOVIE:
        return "movie"
    if fmt is Format.OVA:
        return "ova"
    if fmt is Format.SPECIAL:
        return "special"
    return _REL_LABEL.get(node.rel or "", "related")


def _main_component(ids: list[int], root: int, edges: list[tuple[int, int]],
                    nodes: dict[int, _Node]) -> set[int]:
    """The prequel/sequel chain the root belongs to. When the root is itself a
    side entry (an OVA you opened from a search), its own chain is just itself,
    so the heaviest chain in the map is the main story instead."""
    parent = {i: i for i in ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    comps: dict[int, set[int]] = {}
    for i in ids:
        comps.setdefault(find(i), set()).add(i)

    def weight(c: set[int]) -> int:
        return sum(1 for i in c if nodes[i].anime.format in _MAIN_FORMATS)

    mine = comps[find(root)]
    if len(mine) > 1:
        return mine
    # A root with no prequel/sequel of its own (a movie or OVA opened from a
    # search) sits beside a story line; that line is the main story, not it.
    chains = [c for c in comps.values() if len(c) > 1 and weight(c) > 0]
    if chains:
        # ids is in watch order, so ties go to the earliest chain.
        return max(chains, key=lambda c: (weight(c), -min(ids.index(i) for i in c)))
    return mine


def _effective_total(anime: Anime) -> int | None:
    if anime.episode_count:
        return anime.episode_count
    return 1 if anime.format is Format.MOVIE else None


def _progress_stats(rows) -> tuple[EntryStatus, int]:
    """(status, watched) from one entry's progress rows. A tracker pull stores
    "N episodes done" as a single completed row at episode N, so the count is
    the furthest completed episode, not the number of rows."""
    if not rows:
        return EntryStatus.NOT_STARTED, 0
    done = {r.episode for r in rows if r.completed and r.episode > 0}
    watched = max(int(max(done)), len(done)) if done else 0
    return EntryStatus.IN_PROGRESS, watched


def _suggest(entries: list[FranchiseEntry], current: int) -> int | None:
    if not entries:
        return None
    idx = range(len(entries))
    st = [e.status for e in entries]
    main = [e.main_line for e in entries]

    # (a) you are partway through something: that.
    live = [i for i in idx if st[i] is EntryStatus.IN_PROGRESS]
    if live:
        live_main = [i for i in live if main[i]]
        return (live_main or live)[-1]

    done = [i for i in idx if st[i] is EntryStatus.COMPLETED]
    if not done:
        # (c) nothing watched anywhere: where you came in, or the beginning.
        if main[current]:
            return current
        return next((i for i in idx if main[i]), current)

    # (b) what comes after the furthest chapter you have finished, wherever
    # you started. Side stories never outrank an unwatched main-line entry.
    anchor = ([i for i in done if main[i]] or done)[-1]
    nxt = next((i for i in idx if i > anchor and main[i]
                and st[i] is EntryStatus.NOT_STARTED), None)
    if nxt is not None:
        return nxt
    # (d) the main line is done: a side story, or nothing.
    return next((i for i in idx if not main[i]
                 and st[i] is EntryStatus.NOT_STARTED), None)


# --------------------------------------------------------------------------- #
# Episode metadata parsing (pure; the HTTP lives in the AniList source)
# --------------------------------------------------------------------------- #
_EP_TITLE = re.compile(
    r"^\s*(?:episode|ep\.?|e)\s*(\d+)\s*[-\u2013\u2014:.]\s*(\S.*?)\s*$", re.I
)
_PLACEHOLDER = re.compile(r"^\s*(?:episode|ep\.?)\s*\d+\s*$", re.I)


def parse_streaming_titles(titles) -> dict[int, str]:
    """AniList ``streamingEpisodes`` titles ("Episode 3 - A Title") as
    ``{3: "A Title"}``. Anything that does not parse is skipped; the first
    title for an episode wins (several streaming sites list the same one)."""
    out: dict[int, str] = {}
    for raw in titles or ():
        m = _EP_TITLE.match(raw or "")
        if m:
            out.setdefault(int(m.group(1)), m.group(2))
    return out


def parse_jikan_episodes(rows) -> dict[int, EpisodeInfo]:
    """Jikan v4 ``/anime/{id}/episodes`` rows as EpisodeInfo by number."""
    out: dict[int, EpisodeInfo] = {}
    for r in rows or ():
        try:
            n = int(r["mal_id"])
        except (KeyError, TypeError, ValueError):
            continue
        title = (r.get("title") or "").strip() or None
        if title and _PLACEHOLDER.match(title):
            title = None
        aired = r.get("aired")
        out[n] = EpisodeInfo(
            number=n,
            title=title,
            aired=aired[:10] if isinstance(aired, str) and len(aired) >= 10 else None,
            filler=bool(r.get("filler")),
            recap=bool(r.get("recap")),
        )
    return out


def merge_episode_info(
    titles: dict[int, str], extra: dict[int, EpisodeInfo]
) -> dict[int, EpisodeInfo]:
    """AniList titles win; Jikan fills gaps and supplies air dates and flags."""
    out: dict[int, EpisodeInfo] = {}
    for n in sorted(set(titles) | set(extra)):
        e = extra.get(n)
        out[n] = EpisodeInfo(
            number=n,
            title=titles.get(n) or (e.title if e else None),
            aired=e.aired if e else None,
            filler=e.filler if e else False,
            recap=e.recap if e else False,
        )
    return out


@dataclass
class FranchiseService:
    """Builds `Franchise` maps and records watched-elsewhere progress.

    The TUI reaches it as ``app.services.franchise`` (None-safe: older wiring
    may lack it). ``metadata`` is the AniList source (duck-typed: needs
    ``relations``; ``streaming_episode_titles`` / ``jikan_episodes`` are
    optional); ``library`` is the LibraryService.
    """
    metadata: object
    library: object
    _cache: dict = field(default_factory=dict, repr=False)
    _member_of: dict = field(default_factory=dict, repr=False)
    _episodes: dict = field(default_factory=dict, repr=False)

    # -- build -------------------------------------------------------------- #
    async def build(self, anime: Anime) -> Franchise:
        built = self._lookup(anime)
        if built is None:
            built = await self._walk(anime)
        return await self._assemble(anime, *built)

    def _lookup(self, anime: Anime):
        root_key = self._member_of.get(anime.id.key)
        return self._cache.get(root_key) if root_key else None

    @staticmethod
    def _solo(anime: Anime) -> list:
        """The map when nothing about the franchise is known: just this entry."""
        main = anime.format in _MAIN_FORMATS
        return [(anime, _kind(_Node(anime, None), main), main)]

    async def _walk(self, anime: Anime):
        root = anime.id.anilist
        relations = getattr(self.metadata, "relations", None)
        if root is None or relations is None:
            return self._solo(anime), False

        nodes: dict[int, _Node] = {root: _Node(anime, None)}
        chain: list[tuple[int, int]] = []
        truncated = False
        failures = 0
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def fetch(aid: int):
            async with sem:
                return await relations(nodes[aid].anime.id)

        frontier = [root]
        fetches = 0
        while frontier:
            # Main-line neighbours first so the cap trims side branches, not
            # the prequel/sequel chain.
            frontier.sort(key=lambda i: 0 if nodes[i].rel in (None, *_CHAIN) else 1)
            room = MAX_RELATION_FETCHES - fetches
            if room <= 0:
                truncated = True
                break
            batch = frontier[:room]
            truncated |= len(frontier) > room
            fetches += len(batch)
            results = await asyncio.gather(
                *(fetch(i) for i in batch), return_exceptions=True
            )
            frontier = []
            for aid, res in zip(batch, results):
                if isinstance(res, BaseException):
                    failures += 1
                    log.debug("relations(%s) failed: %s", aid, res)
                    if aid == root:
                        # No map at all: just the entry that was asked for.
                        return self._solo(anime), False
                    continue
                for rel, other in res:
                    oid = other.id.anilist
                    if rel not in _KEEP or oid is None or other.format is Format.MUSIC:
                        continue
                    if rel in _CHAIN and oid in nodes:
                        chain.append((aid, oid))
                    if oid in nodes:
                        # The root usually arrives from the library with no
                        # start date; a neighbour's back-edge carries one.
                        if nodes[oid].anime.start_date is None and other.start_date:
                            nodes[oid].anime = replace(
                                nodes[oid].anime, start_date=other.start_date)
                        continue
                    if len(nodes) >= MAX_ENTRIES:
                        truncated = True
                        continue
                    nodes[oid] = _Node(other, rel, expand=rel in _WALK)
                    if rel in _CHAIN:
                        chain.append((aid, oid))
                    if rel in _WALK:
                        frontier.append(oid)
        truncated |= failures > 0

        ordered_ids = sorted(nodes, key=lambda i: _sort_key(nodes[i]))
        main_ids = _main_component(ordered_ids, root, chain, nodes)
        entries = []
        for i in ordered_ids:
            n = nodes[i]
            main = i in main_ids and n.anime.format in _MAIN_FORMATS
            entries.append((n.anime, _kind(n, main), main))
        if failures == 0:
            root_key = anime.id.key
            self._cache[root_key] = (entries, truncated)
            for a, _, _ in entries:
                self._member_of.setdefault(a.id.key, root_key)
        return entries, truncated

    async def _progress(self, anime: Anime):
        try:
            return await self.library.progress_for(anime.id)
        except Exception as e:  # never let a library hiccup blank the screen
            log.debug("progress_for(%s) failed: %s", anime.id.key, e)
            return []

    async def _assemble(self, anime: Anime, raw, truncated: bool) -> Franchise:
        rows = await asyncio.gather(*(self._progress(a) for a, _, _ in raw))
        entries: list[FranchiseEntry] = []
        current = 0
        for i, ((a, kind, main), prog) in enumerate(zip(raw, rows)):
            if a.id.key == anime.id.key:
                # The caller's copy may be fresher; keep the date the walk found.
                a = replace(anime, start_date=anime.start_date or a.start_date)
                current = i
            status, watched = _progress_stats(prog)
            total = _effective_total(a)
            if status is EntryStatus.IN_PROGRESS and total and watched >= total:
                status = EntryStatus.COMPLETED
            entries.append(FranchiseEntry(
                anime=a, kind=kind, main_line=main, status=status,
                watched=watched, total=a.episode_count,
                is_current=a.id.key == anime.id.key,
            ))
        return Franchise(
            entries=tuple(entries), current_index=current,
            suggested_index=_suggest(entries, current), truncated=truncated,
        )

    # -- mark --------------------------------------------------------------- #
    async def mark_entry_watched(self, anime: Anime) -> int:
        """Mark every episode of one entry watched (seen elsewhere).
        Returns how many episodes were recorded."""
        total = _effective_total(anime)
        name = anime.title.preferred
        if anime.is_airing:
            aired = anime.aired_through()
            if aired is None:
                raise AnimeShError(
                    f"{name} is still airing and has no schedule, so there is "
                    "no telling how many episodes are out. Mark it from its "
                    "own page once you know the episode."
                )
            count = min(aired, total) if total else aired
        elif total is None:
            raise AnimeShError(
                f"{name} has no known episode count, so it can't be marked "
                "watched in one go. Mark it from its own page instead."
            )
        else:
            count = total
        if count < 1:
            raise AnimeShError(f"{name} has no episodes out yet.")
        marked = await self.library.mark_watched(anime, up_to=count)
        return len(marked)

    # -- episode info ------------------------------------------------------- #
    async def episode_info(self, anime: Anime) -> dict[int, EpisodeInfo]:
        """Episode titles/air dates/filler flags, best effort; {} on failure."""
        key = anime.id.key
        if key in self._episodes:
            return self._episodes[key]
        titles_fn = getattr(self.metadata, "streaming_episode_titles", None)
        jikan_fn = getattr(self.metadata, "jikan_episodes", None)

        async def titles():
            if titles_fn is None or anime.id.anilist is None:
                return {}
            return parse_streaming_titles(await titles_fn(anime.id))

        async def extra():
            if jikan_fn is None or anime.id.mal is None:
                return {}
            return parse_jikan_episodes(await jikan_fn(anime.id.mal))

        a, b = await asyncio.gather(titles(), extra(), return_exceptions=True)
        for r in (a, b):
            if isinstance(r, BaseException):
                log.debug("episode info for %s: %s", key, r)
        a = {} if isinstance(a, BaseException) else a
        b = {} if isinstance(b, BaseException) else b
        out = merge_episode_info(a, b)
        if out:  # an empty answer may just be a bad moment; don't pin it
            self._episodes[key] = out
        return out
