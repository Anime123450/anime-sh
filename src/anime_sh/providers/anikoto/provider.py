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
        for query in _search_terms(anime):
            html = await self._get_text("/search", {"keyword": query})
            items = parse_search(html)
            best = _best_match(anime, items)
            if best is not None:
                return ProviderRef(
                    provider=self.name, anime_key=best["id"], audio=audio,
                    confidence=best["_score"],
                )
        return None

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


def parse_search(html: str) -> list[dict]:
    """Parse search result cards into {id, url, jp, title}."""
    items: list[dict] = []
    for chunk in html.split('class="item "')[1:]:
        tip = _TIP_RE.search(chunk)
        title = _DTITLE_RE.search(chunk)
        if not (tip and title):
            continue
        items.append(
            {
                "id": tip.group(1),
                "url": html_lib.unescape(title.group(1)),
                "jp": html_lib.unescape(title.group(2)),
                "title": html_lib.unescape(title.group(3).strip()),
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


def _best_match(anime: Anime, items: list[dict]) -> dict | None:
    targets = [
        _norm(t)
        for t in (anime.title.romaji, anime.title.english, *anime.title.synonyms)
        if t
    ]
    best, best_score = None, 0.0
    for item in items:
        names = [item.get("title"), item.get("jp")]
        score = max(
            (
                SequenceMatcher(None, _norm(n), tgt).ratio()
                for n in names
                if n
                for tgt in targets
            ),
            default=0.0,
        )
        if score > best_score:
            best, best_score = item, score
    if best is None or best_score < 0.6:
        return None
    best = dict(best)
    best["_score"] = round(best_score, 3)
    return best


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
