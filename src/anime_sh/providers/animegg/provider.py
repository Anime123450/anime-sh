"""AnimeGG provider (animegg.org) — server-rendered HTML, direct MP4.

Reverse-engineered flow (plain HTTP, no Cloudflare challenge, no encryption):

1. ``GET /search?q=<title>``      → ``<a href="/series/<slug>" class="mse">`` cards
   with an ``<h2>`` title, ``Episodes: N`` and optional ``Alt Titles : a; b``.
2. ``GET /series/<slug>``         → episode links ``/<slug>-episode-N``; each row
   also links ``…#subbed`` and/or ``…#dubbed`` for the audio it actually has.
3. ``GET /<slug>-episode-N``      → one ``<div id="subbed-<Mirror>">`` /
   ``<div id="dubbed-<Mirror>">`` pane per audio, each holding an
   ``<iframe src="/embed/<id>">``. Sub and dub have *separate* embed ids.

The embed page is handed on as a candidate (host ``animegg``); the
:mod:`anime_sh.resolvers.animegg` resolver unwraps it into one MP4 stream per
quality. That step is deliberately not done here: the ``/play/…`` URLs carry a
token minted on every fetch of the embed page, so they must be fetched at play
time, not cached with the episode listing.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from html import unescape
from urllib.parse import urljoin

from ...domain.errors import ProviderError, ProviderUnavailable
from ...domain.models import (
    Anime,
    AnimeId,
    Audio,
    Episode,
    ProviderRef,
    SourceOption,
    StreamCandidate,
)
from ...infra.http import CloudflareChallenge, HttpClient, HttpError
from .. import _matching as m

log = logging.getLogger(__name__)

BASE = "https://www.animegg.org"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

# Source-picker candidates whose series page we double-check for a dub.
_DUB_CHECK_LIMIT = 4


class AnimeggProvider:
    name = "animegg"
    # Below anizone (90), hianime (80) and anikoto (70): AnimeGG is a fallback
    # with a modest library, though its direct MP4s need no third-party embed.
    priority = 60
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        # Plain httpx — the site is not Cloudflare-gated.
        self._http = http or HttpClient(
            headers={"User-Agent": AGENT, "Referer": f"{BASE}/"}
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- transport ---------------------------------------------------------- #
    async def _get(self, path: str, params: dict | None = None) -> str:
        try:
            return await self._http.get_text(f"{BASE}{path}", params=params)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"animegg: {e}") from e
        except HttpError as e:
            raise ProviderError(f"animegg request failed: {e}") from e

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        sources = await self.find_sources(anime, audio)
        return sources[0].ref() if sources else None

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        found: dict[str, dict] = {}
        scored: list[dict] = []
        for query in m.search_terms(anime):
            html = await self._get("/search", {"q": query})
            for item in parse_search(html):
                found.setdefault(item["slug"], item)
            scored = _score(anime, list(found.values()))
            if scored:
                break
        ranked = m.rank_items(anime, scored)

        if audio is Audio.DUB:
            # Search cards say nothing about dubs, so confirm on the series page.
            # Only a few dubs exist; declining here lets the fan-out fall
            # through to a dub-capable provider rather than serve sub as dub.
            checked: list[dict] = []
            for item in ranked[:_DUB_CHECK_LIMIT]:
                page = await self._get(f"/series/{item['slug']}")
                if any(e["dub"] for e in parse_episodes(page, item["slug"])):
                    checked.append(item)
            ranked = checked

        return [
            SourceOption(
                provider=self.name, anime_key=it["slug"], title=it["title"],
                episode_count=it.get("sub_eps"), audio=audio,
                confidence=it["_score"],
            )
            for it in ranked
        ]

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        html = await self._get(f"/series/{ref.anime_key}")
        want_dub = ref.audio is Audio.DUB
        out: dict[float, Episode] = {}
        for ep in parse_episodes(html, ref.anime_key):
            if want_dub and not ep["dub"]:
                continue
            if not want_dub and not ep["sub"]:
                continue
            out.setdefault(
                ep["number"],
                Episode(anime_id=anime_id, number=ep["number"], provider_ref=ref,
                        episode_key=f"{ep['number']:g}"),
            )
        return sorted(out.values(), key=lambda e: e.number)

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        ref = episode.provider_ref
        path = f"/{ref.anime_key}-episode-{episode.episode_key}"
        html = await self._get(path)
        version = "dubbed" if ref.audio is Audio.DUB else "subbed"
        return [
            StreamCandidate(
                host="animegg",
                url=url,
                audio=ref.audio,
                # The embed page expects to be opened from the episode page.
                headers={"Referer": f"{BASE}{path}"},
            )
            for url in parse_embeds(html, version)
        ]


# --------------------------------------------------------------------------- #
# Pure HTML parsing (unit-tested without network)
# --------------------------------------------------------------------------- #
_CARD_RE = re.compile(
    r'<a\s+href="/series/([^"/#?]+)"[^>]*class="mse"[^>]*>(.*?)</a>', re.DOTALL
)
_H2_RE = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)
_EPS_RE = re.compile(r"Episodes:\s*(\d+)")
_ALT_RE = re.compile(r"Alt Titles\s*:\s*([^<]+)")
_EP_LINK_RE = re.compile(
    r'href="/(?P<slug>[^"/#?]+)-episode-(?P<num>\d+(?:\.\d+)?)(?:#(?P<ver>subbed|dubbed))?"'
)
_PANE_RE = re.compile(
    r'id="(?P<ver>subbed|dubbed)-[^"]*"[^>]*>\s*<iframe\b[^>]*\bsrc="(?P<src>[^"]+)"'
)


def parse_search(html: str) -> list[dict]:
    """Result cards → {slug, title, alts, sub_eps}. Site order preserved."""
    out, seen = [], set()
    for slug, body in _CARD_RE.findall(html):
        if slug in seen:
            continue
        seen.add(slug)
        h2 = _H2_RE.search(body)
        eps = _EPS_RE.search(body)
        alt = _ALT_RE.search(body)
        out.append({
            "slug": slug,
            "title": unescape(h2.group(1)).strip() if h2 else slug,
            "alts": [a.strip() for a in unescape(alt.group(1)).split(";") if a.strip()]
            if alt else [],
            "sub_eps": int(eps.group(1)) if eps else None,
        })
    return out


def parse_episodes(html: str, slug: str) -> list[dict]:
    """Series page → [{number, sub, dub}], scoped to ``slug``.

    ``sub``/``dub`` come from the ``#subbed``/``#dubbed`` links in the row. A
    row that carries neither marker is treated as sub (the default audio).
    """
    rows: dict[float, dict] = {}
    for mt in _EP_LINK_RE.finditer(html):
        if mt.group("slug") != slug:
            continue
        num = float(mt.group("num"))
        row = rows.setdefault(num, {"number": num, "sub": False, "dub": False})
        ver = mt.group("ver")
        if ver == "subbed":
            row["sub"] = True
        elif ver == "dubbed":
            row["dub"] = True
    for row in rows.values():
        if not row["sub"] and not row["dub"]:
            row["sub"] = True
    return [rows[n] for n in sorted(rows)]


def parse_embeds(html: str, version: str) -> list[str]:
    """Episode page → absolute embed URLs for ``version`` ("subbed"/"dubbed")."""
    out: list[str] = []
    for mt in _PANE_RE.finditer(html):
        if mt.group("ver") != version:
            continue
        url = urljoin(f"{BASE}/", unescape(mt.group("src")))
        if url not in out:
            out.append(url)
    return out


def _score(anime: Anime, items: list[dict]) -> list[dict]:
    """Fuzzy-match each card (title + alt titles) against every AniList title;
    keep those over the shared threshold, tagged with ``_score``."""
    targets = [
        m.norm(t)
        for t in (anime.title.romaji, anime.title.english, *anime.title.synonyms)
        if t
    ]
    out: list[dict] = []
    for item in items:
        sim = max(
            (
                SequenceMatcher(None, m.norm(n), t).ratio()
                for n in (item["title"], *item.get("alts", ())) if n
                for t in targets
            ),
            default=0.0,
        )
        if sim >= m.MATCH_THRESHOLD:
            item = dict(item)
            item["_score"] = round(sim, 3)
            out.append(item)
    return out
