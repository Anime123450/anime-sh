"""AniZone provider (anizone.to) — clean, plaintext HLS.

Reverse-engineered flow (all plain HTTP, no Cloudflare, no obfuscation):

1. ``GET /anime?search=<q>``        → an Alpine ``items: JSON.parse('[…]')`` list of
   ``{slug, main_title, title_list}`` (older markup: Livewire cards with
   ``wire:key="a-<id>"`` — still parsed as a fallback).
2. ``GET /anime/<id>``              → the show page. Since 2026-11 it lazy-loads its
   episode list over Livewire, so it may link only one episode; that episode's
   page (``/anime/<id>/<n>``) renders the *full* episode sidebar, so we read it too.
3. ``GET /anime/<id>/<n>``          → ``vidstackPlayer(JSON.parse('{src, subtitles…}'))``
   with the master.m3u8 and soft ``.ass`` tracks (older markup:
   ``<media-player src>`` + ``<track>`` tags, still parsed as a fallback).

The m3u8 plays directly (the generic resolver passes it through), and its CDN is
reachable without a referer — which is exactly why AniZone works where
Cloudflare-gated sites don't. Subtitles are soft ``.ass`` tracks attached to the
candidate.
"""

from __future__ import annotations

import html as html_lib
import json
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
    # Highest priority: it hands over a direct, un-obfuscated m3u8 that plays
    # without a proxy or a third-party embed host, so it's the most reliable.
    priority = 90
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        # Plain httpx is enough — AniZone isn't Cloudflare-gated, so we skip the
        # curl_cffi impersonation (and its cold-start, which could blow the
        # source-listing timeout on the first search of a session).
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
            raise ProviderUnavailable(f"anizone: {e}") from e
        except HttpError as e:
            raise ProviderError(f"anizone request failed: {e}") from e

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        sources = await self.find_sources(anime, audio)
        return sources[0].ref() if sources else None

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        # AniZone is sub-only (soft English subs). Decline dub requests instead
        # of serving sub content mislabelled as dub — this lets the fan-out fall
        # through to providers that actually have a dub (hianime).
        if audio is Audio.DUB:
            return []
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
        nums = set(parse_episode_numbers(html, ref.anime_key))
        if len(nums) < 2:
            # The show page lazy-loads its list; an episode page carries all of it.
            first = min(nums) if nums else 1.0
            try:
                page = await self._get(f"/anime/{ref.anime_key}/{first:g}")
            except ProviderError:
                page = ""
            nums |= set(parse_episode_numbers(page, ref.anime_key))
        episodes: list[Episode] = []
        for num in sorted(nums):
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
_ITEMS_RE = re.compile(r"items:\s*JSON\.parse\('((?:[^'\\]|\\.)*)'\)")
_PLAYER_RE = re.compile(r"vidstackPlayer\(JSON\.parse\('((?:[^'\\]|\\.)*)'\)")


def _js_json(raw: str):
    """Decode the argument of an inline ``JSON.parse('…')``: a single-quoted JS
    string literal (inside an HTML attribute) whose value is JSON."""
    for cand in (html_lib.unescape(raw), raw):
        try:
            text = json.loads('"' + cand.replace('"', '\\"').replace("\\'", "'") + '"')
            return json.loads(text)
        except ValueError:
            continue
    return None


def _embedded(pattern: re.Pattern[str], html: str):
    m = pattern.search(html)
    return _js_json(m.group(1)) if m else None


def _attr(tag: str, name: str) -> str | None:
    """Read an attribute from a tag string — quoted or (as AniZone does for
    ``src``) unquoted."""
    m = re.search(rf'{name}="([^"]*)"', tag) or re.search(rf"{name}=(\S+)", tag)
    return m.group(1).rstrip("/>") if m else None


def parse_search(html: str) -> list[dict]:
    """Search results → {id, title, titles}. Order preserved (site's best first)."""
    out, seen = [], set()
    items = _embedded(_ITEMS_RE, html)
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict) or not it.get("slug") or it["slug"] in seen:
                continue
            seen.add(it["slug"])
            main = str(it.get("main_title") or "").strip()
            alts = it.get("title_list") or {}
            alts = list(alts.values()) if isinstance(alts, dict) else list(alts)
            titles = [t for t in dict.fromkeys([main, *map(str, alts)]) if t]
            out.append({"id": it["slug"], "title": main or (titles[0] if titles else ""),
                        "titles": titles})
        if out:
            return out
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
    player = _embedded(_PLAYER_RE, html)
    if isinstance(player, dict) and ".m3u8" in str(player.get("src") or ""):
        return str(player["src"])
    m = _STREAM_RE.search(html)
    return m.group(1) if m else None


def parse_subtitles(html: str) -> tuple[Subtitle, ...]:
    """Soft-sub tracks. AniZone ships every language (15+); we keep only English
    (that's what this client is for) so mpv doesn't fetch a dozen .ass files, and
    flag one default. Falls back to the site default / first track if there is no
    English track at all."""
    all_subs: list[Subtitle] = []
    player = _embedded(_PLAYER_RE, html)
    for t in (player or {}).get("subtitles") or [] if isinstance(player, dict) else []:
        if isinstance(t, dict) and t.get("file"):
            all_subs.append(Subtitle(url=str(t["file"]), lang=str(t.get("language") or "und"),
                                     label=t.get("title"), default=bool(t.get("default"))))
    for tag in ([] if all_subs else _TRACK_TAG_RE.findall(html)):
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
        names = [_norm(n) for n in item.get("titles") or [item["title"]]]
        sim = max((SequenceMatcher(None, n, t).ratio() for n in names if n for t in targets),
                  default=0.0)
        if sim >= _MATCH_THRESHOLD:
            item = dict(item)
            item["_score"] = round(sim, 3)
            scored.append(item)
    return sorted(scored, key=lambda i: -i["_score"])
