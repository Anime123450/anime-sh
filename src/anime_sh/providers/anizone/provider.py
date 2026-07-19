"""AniZone provider (anizone.to) — clean, plaintext HLS.

Reverse-engineered flow (all plain HTTP, no Cloudflare, no obfuscation):

1. ``GET /anime?search=<q>``        → result cards; each Livewire card carries
   ``wire:key="a-<id>"`` and a ``getTitle(this.anmTitles, '<title>')`` default.
2. ``GET /anime/<id>``              → episode links ``/anime/<id>/<n>``.
3. ``GET /anime/<id>/<n>``          → a ``<media-player src="…master.m3u8">`` and
   ``<track …>`` subtitle tags right in the page.

The m3u8 plays directly (the generic resolver passes it through), and its CDN is
reachable without a referer — which is exactly why AniZone works where
Cloudflare-gated sites don't. Subtitles are soft ``.ass`` tracks attached to the
candidate.
"""

from __future__ import annotations

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
    StreamCandidate,
    Subtitle,
)
from ...infra.http import CloudflareChallenge, HttpClient, HttpError

log = logging.getLogger(__name__)

BASE = "https://anizone.to"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"


def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())


class AnizoneProvider:
    name = "anizone"
    priority = 70
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        # Plain httpx is enough — AniZone isn't Cloudflare-gated, so we skip the
        # curl_cffi impersonation (and its cold-start, which could blow the
        # source-listing timeout on the first search of a session).
        self._http = http or HttpClient(
            headers={"User-Agent": AGENT, "Referer": f"{BASE}/"}
        )

    # -- transport ---------------------------------------------------------- #
    async def _get(self, path: str, params: dict | None = None) -> str:
        try:
            return await self._http.get_text(f"{BASE}{path}", params=params)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"anizone: {e}") from e
        except HttpError as e:
            raise ProviderError(f"anizone request failed: {e}") from e

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        sources = await self.find_sources(anime, audio)
        return sources[0].ref() if sources else None

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        seen: dict[str, dict] = {}
        for query in _search_terms(anime):
            html = await self._get("/anime", {"search": query})
            for item in parse_search(html):
                seen.setdefault(item["id"], item)
            if seen:
                break
        return [
            SourceOption(
                provider=self.name, anime_key=it["id"], title=it["title"],
                episode_count=None, audio=audio, confidence=it["_score"],
            )
            for it in _ranked(anime, list(seen.values()))
        ]

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        html = await self._get(f"/anime/{ref.anime_key}")
        episodes: list[Episode] = []
        for num in parse_episode_numbers(html, ref.anime_key):
            episodes.append(
                Episode(anime_id=anime_id, number=num, provider_ref=ref,
                        episode_key=f"{num:g}")
            )
        episodes.sort(key=lambda e: e.number)
        return episodes

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        ref = episode.provider_ref
        html = await self._get(f"/anime/{ref.anime_key}/{episode.episode_key}")
        url = parse_stream_url(html)
        if not url:
            return []
        return [
            StreamCandidate(
                host="anizone",
                url=url,
                audio=ref.audio,
                subtitles=parse_subtitles(html),
            )
        ]


# --------------------------------------------------------------------------- #
# Pure HTML parsing (unit-tested without network)
# --------------------------------------------------------------------------- #
_CARD_RE = re.compile(
    r"getTitle\(this\.anmTitles,\s*'([^']*)'\)[\s\S]{0,600}?wire:key=\"a-([^\"]+)\""
)
_STREAM_RE = re.compile(r'<media-player\b[^>]*\bsrc="([^"]+\.m3u8[^"]*)"')
_TRACK_TAG_RE = re.compile(r"<track\b[^>]*>")


def _attr(tag: str, name: str) -> str | None:
    """Read an attribute from a tag string — quoted or (as AniZone does for
    ``src``) unquoted."""
    m = re.search(rf'{name}="([^"]*)"', tag) or re.search(rf"{name}=(\S+)", tag)
    return m.group(1).rstrip("/>") if m else None


def parse_search(html: str) -> list[dict]:
    """Result cards → {id, title}. Order preserved (best match first on site)."""
    out, seen = [], set()
    for title, aid in _CARD_RE.findall(html):
        if aid in seen:
            continue
        seen.add(aid)
        out.append({"id": aid, "title": title.strip()})
    return out


def parse_episode_numbers(html: str, anime_id: str) -> list[float]:
    nums: set[float] = set()
    for m in re.finditer(rf'/anime/{re.escape(anime_id)}/(\d+(?:\.\d+)?)', html):
        try:
            nums.add(float(m.group(1)))
        except ValueError:
            continue
    return sorted(nums)


def parse_stream_url(html: str) -> str | None:
    m = _STREAM_RE.search(html)
    return m.group(1) if m else None


def parse_subtitles(html: str) -> tuple[Subtitle, ...]:
    """Soft-sub tracks. AniZone ships every language (15+); we keep only English
    (that's what this client is for) so mpv doesn't fetch a dozen .ass files, and
    flag one default. Falls back to the site default / first track if there is no
    English track at all."""
    all_subs: list[Subtitle] = []
    for tag in _TRACK_TAG_RE.findall(html):
        if 'kind="subtitles"' not in tag:
            continue
        src = _attr(tag, "src")
        if not src:
            continue
        all_subs.append(
            Subtitle(
                url=src.strip("\"'"),
                lang=_attr(tag, "srclang") or "und",
                label=_attr(tag, "label"),
                default=re.search(r"\bdefault\b(?!=)", tag) is not None,
            )
        )
    english = [s for s in all_subs if s.lang.lower().startswith("en")]
    subs = english or (
        [s for s in all_subs if s.default] or all_subs[:1]
    )
    if subs and not any(s.default for s in subs):
        first = subs[0]
        subs[0] = Subtitle(first.url, first.lang, first.label, default=True)
    return tuple(subs)


def _search_terms(anime: Anime) -> list[str]:
    terms = [anime.title.romaji, anime.title.english, *anime.title.synonyms]
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out or [anime.title.preferred]


_MATCH_THRESHOLD = 0.55


def _ranked(anime: Anime, items: list[dict]) -> list[dict]:
    """Fuzzy-matched cards, best title first."""
    targets = [
        _norm(t)
        for t in (anime.title.romaji, anime.title.english, *anime.title.synonyms)
        if t
    ]
    scored: list[dict] = []
    for item in items:
        sim = max((SequenceMatcher(None, _norm(item["title"]), t).ratio() for t in targets),
                  default=0.0)
        if sim >= _MATCH_THRESHOLD:
            item = dict(item)
            item["_score"] = round(sim, 3)
            scored.append(item)
    return sorted(scored, key=lambda i: -i["_score"])
