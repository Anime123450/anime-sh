"""Anikoto provider (anikototv.to) — a HiAnime-family site.

Reverse-engineered flow:

1. ``GET /search?keyword=…``            → HTML result cards (``data-tip`` = anime id)
2. ``GET /ajax/episode/list/<id>``      → JSON whose ``result`` is episode HTML;
   each episode ``<a>`` carries ``data-num``, ``data-mal``, ``data-sub/dub`` and
   a ``data-ids`` server-token blob.
3. ``GET /ajax/server/list?servers=<data-ids>`` → JSON server-list HTML; each
   ``<li>`` has a name, ``data-type`` (sub/dub) and a ``data-link-id`` token.
4. ``GET /ajax/server?get=<data-link-id>``      → ``{url: <embed>, skip_data}``.

The provider returns each embed URL (e.g. vidtube.site) as a candidate;
resolvers turn those into playable streams. Anikoto covers shows AllAnime
lacks — the whole reason for fanning out across providers.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from difflib import SequenceMatcher

from ...domain.errors import ProviderError, ProviderUnavailable
from ...domain.models import (
    Anime,
    AnimeId,
    Audio,
    Episode,
    ProviderRef,
    SourceOption,
    Status,
    StreamCandidate,
)
from ...infra.http import CloudflareChallenge, HttpClient, HttpError

log = logging.getLogger(__name__)

BASE = "https://anikototv.to"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"


def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())


class AnikotoProvider:
    name = "anikoto"
    priority = 80
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(
            headers={
                "User-Agent": AGENT,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE}/",
            }
        )

    # -- transport ---------------------------------------------------------- #
    async def _get_text(self, path: str, params: dict | None = None) -> str:
        try:
            return await self._http.get_text(f"{BASE}{path}", params=params)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"anikoto: {e}") from e
        except HttpError as e:
            raise ProviderError(f"anikoto request failed: {e}") from e

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        try:
            data = await self._http.get_json(f"{BASE}{path}", params=params)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"anikoto: {e}") from e
        except HttpError as e:
            raise ProviderError(f"anikoto request failed: {e}") from e
        if not isinstance(data, dict):
            raise ProviderError(f"anikoto: unexpected response {str(data)[:120]}")
        return data

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        sources = await self.find_sources(anime, audio)
        return sources[0].ref() if sources else None

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        seen: dict[str, dict] = {}
        for query in _search_terms(anime):
            html = await self._get_text("/search", {"keyword": query})
            for item in _scored_matches(anime, parse_search(html)):
                seen.setdefault(item["id"], item)  # dedupe across query terms
            if seen:
                break
        return [
            SourceOption(
                provider=self.name, anime_key=it["id"], title=it["title"],
                episode_count=it.get("sub_eps"), audio=audio, confidence=it["_score"],
            )
            for it in _rank_items(anime, list(seen.values()))
        ]

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        data = await self._get_json(f"/ajax/episode/list/{ref.anime_key}")
        want_dub = ref.audio is Audio.DUB
        episodes: list[Episode] = []
        for ep in parse_episodes(data.get("result", "")):
            if want_dub and not ep["dub"]:
                continue
            if not want_dub and not ep["sub"]:
                continue
            episodes.append(
                Episode(
                    anime_id=anime_id,
                    number=ep["num"],
                    provider_ref=ref,
                    # episode_key carries the server-token blob candidates need.
                    episode_key=ep["ids"],
                )
            )
        episodes.sort(key=lambda e: e.number)
        return episodes

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        ref = episode.provider_ref
        want = "dub" if ref.audio is Audio.DUB else "sub"
        data = await self._get_json(
            "/ajax/server/list", {"servers": episode.episode_key}
        )
        out: list[StreamCandidate] = []
        for server in parse_servers(data.get("result", "")):
            if server["type"] != want:
                continue
            embed = await self._server_url(server["link_id"])
            if not embed:
                continue
            out.append(
                StreamCandidate(
                    host=server["name"],
                    url=embed,
                    audio=ref.audio,
                    headers={"Referer": f"{BASE}/"},
                )
            )
        return out

    async def _server_url(self, link_id: str) -> str | None:
        data = await self._get_json("/ajax/server", {"get": link_id})
        result = data.get("result")
        if isinstance(result, dict):
            return result.get("url")
        return None


# --------------------------------------------------------------------------- #
# Pure HTML parsing + matching (unit-tested without network)
# --------------------------------------------------------------------------- #
_DTITLE_RE = re.compile(
    r'class="name d-title"[^>]*?href="([^"]+)"[^>]*?data-jp="([^"]*)"[^>]*?>([^<]+)</a>'
)
_TIP_RE = re.compile(r'data-tip="(\d+)"')
_EP_ANCHOR_RE = re.compile(r"<a\b([^>]*\bdata-id=\"\d+\"[^>]*)>")
_ATTR_RE = re.compile(r'([a-zA-Z-]+)="([^"]*)"')
_LI_RE = re.compile(r"<li\b([^>]*)>([^<]+)</li>")


_SUBEPS_RE = re.compile(r'ep-status sub">\s*<span>\s*(\d+)')


def parse_search(html: str) -> list[dict]:
    """Parse search result cards into {id, url, jp, title, sub_eps}."""
    items: list[dict] = []
    for chunk in html.split('class="item "')[1:]:
        tip = _TIP_RE.search(chunk)
        title = _DTITLE_RE.search(chunk)
        if not (tip and title):
            continue
        sub = _SUBEPS_RE.search(chunk)
        items.append(
            {
                "id": tip.group(1),
                "url": html_lib.unescape(title.group(1)),
                "jp": html_lib.unescape(title.group(2)),
                "title": html_lib.unescape(title.group(3).strip()),
                "sub_eps": int(sub.group(1)) if sub else None,
            }
        )
    return items


def parse_episodes(result_html: str) -> list[dict]:
    """Parse the episode-list HTML into {num, ep_id, ids, sub, dub, mal}."""
    eps: list[dict] = []
    for m in _EP_ANCHOR_RE.finditer(result_html):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        num = _to_float(attrs.get("data-num"))
        ids = attrs.get("data-ids")
        if num is None or not ids:
            continue
        eps.append(
            {
                "num": num,
                "ep_id": attrs.get("data-id"),
                "ids": ids,
                "sub": attrs.get("data-sub") == "1",
                "dub": attrs.get("data-dub") == "1",
                "mal": attrs.get("data-mal"),
            }
        )
    return eps


def parse_servers(result_html: str) -> list[dict]:
    """Parse the server-list HTML into {type, name, sv_id, link_id}."""
    servers: list[dict] = []
    for tm in re.finditer(
        r'data-type="(sub|dub|raw)"(.*?)(?=data-type="|$)', result_html, re.S
    ):
        typ = tm.group(1)
        for lm in _LI_RE.finditer(tm.group(2)):
            attrs = dict(_ATTR_RE.findall(lm.group(1)))
            link_id = attrs.get("data-link-id")
            if not link_id:
                continue
            servers.append(
                {
                    "type": typ,
                    "name": html_lib.unescape(lm.group(2).strip()),
                    "sv_id": attrs.get("data-sv-id"),
                    "link_id": link_id,
                }
            )
    return servers


def _search_terms(anime: Anime) -> list[str]:
    terms = [anime.title.romaji, anime.title.english, *anime.title.synonyms]
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out or [anime.title.preferred]


# Fuzzy gate: keep near-matches, not just exact titles, so alternate entries
# (a "[Mini]" batch, a slightly different romanisation) still surface.
_MATCH_THRESHOLD = 0.55


def _scored_matches(anime: Anime, items: list[dict]) -> list[dict]:
    """Items whose title fuzzily matches the show, each tagged with ``_score``
    (title similarity). Not ranked — see :func:`_rank_items`."""
    targets = [
        _norm(t)
        for t in (anime.title.romaji, anime.title.english, *anime.title.synonyms)
        if t
    ]
    out: list[dict] = []
    for item in items:
        names = [item.get("title"), item.get("jp")]
        sim = max(
            (
                SequenceMatcher(None, _norm(n), tgt).ratio()
                for n in names if n for tgt in targets
            ),
            default=0.0,
        )
        if sim >= _MATCH_THRESHOLD:
            item = dict(item)
            item["_score"] = round(sim, 3)
            out.append(item)
    return out


def _rank_items(anime: Anime, items: list[dict]) -> list[dict]:
    """Order matches best-first: among genuinely-similar titles, prefer the one
    whose available episode count is closest to AniList's planned total (or the
    most complete when unknown) — so a full "[Mini]" batch outranks a same-named
    TV run that Anikoto has stalled on.

    While the show is still AIRING that heuristic inverts: no entry can have the
    planned total yet, so one that does (e.g. a finished "[Mini]" spin-off with
    the same name) is a *different* production. There the closest title wins,
    tie-broken by the most-stocked entry within the planned total."""
    if not items:
        return []
    best_sim = max(it["_score"] for it in items)
    releasing = anime.status is Status.RELEASING

    def rank(item: dict) -> tuple:
        strong = item["_score"] >= best_sim - 0.15
        eps = item.get("sub_eps")
        want = anime.episode_count
        if releasing:
            within = eps if strong and eps and (want is None or eps <= want) else 0
            return (-item["_score"], -within)
        if strong and want and eps:
            ep_key = abs(eps - want)
        elif strong and eps:
            ep_key = -eps
        else:
            ep_key = 10_000
        return (ep_key, -item["_score"])

    return sorted(items, key=rank)


def _best_match(anime: Anime, items: list[dict]) -> dict | None:
    ranked = _rank_items(anime, _scored_matches(anime, items))
    return ranked[0] if ranked else None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
