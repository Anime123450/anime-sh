"""WordPress anime sites that embed Blogger videos (animepahe.ch, zorotv.ba).

Not the real animepahe/kwik — ``animepahe.ch`` is a WordPress site on the
"dramastream" (themesia ``animestream`` family) theme. Everything is plain,
un-gated HTML:

1. ``GET /?s=<title>``                    → ``<article class="bs">`` cards:
   ``<a href=".../series/<slug>/" title="…">`` and a status badge.
2. ``GET /series/<slug>/``                → ``div.eplister`` with one
   ``<li><a href=".../<slug>-episode-N-english-subbed/"><div class="epl-num">N``
   per episode.
3. ``GET /<slug>-episode-N-english-subbed/`` → ``div.player-embed`` holding a
   single ``<iframe src="https://www.blogger.com/video.g?token=…">``.

The iframe URL is the candidate; :mod:`anime_sh.resolvers.blogger` turns it into
direct ``googlevideo.com`` MP4s at play time (they expire, so they are
never stored here). Sub only — the sites carry no dubs.

``zorotv.ba`` (same theme, ``/anime/<slug>/`` series URLs) differs only at the
last step: its episode iframe is a JW Player wrapper
(``/wp-content/themes/animestream-child/player/?source=blogger&url=<blob>``)
that resolves the Blogger video server-side and renders the ``googlevideo.com``
URL into the page. The blob is opaque, so the wrapper URL itself is the
candidate and the Blogger resolver reads the media URL out of the wrapper page.
That path is 360p only and leans on the site's server, so it ranks just below
animepahe.ch. :class:`BloggerSite` is the per-site config.
"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import logging
import re
from dataclasses import dataclass

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
from .._matching import scored_matches, search_terms, to_float

log = logging.getLogger(__name__)

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"


@dataclass(frozen=True, slots=True)
class BloggerSite:
    """Per-site knobs for a WordPress/themesia site that embeds Blogger."""

    name: str  # provider name shown to users, e.g. "animepahe-ch"
    base: str  # "https://animepahe.ch"
    priority: int
    series_prefix: str = "series"  # /series/<slug>/ (zorotv-style sites: "anime")


ANIMEPAHE_CH = BloggerSite(
    name="animepahe-ch", base="https://animepahe.ch", priority=50
)
ZOROTV = BloggerSite(
    name="zorotv", base="https://zorotv.ba", priority=49, series_prefix="anime"
)


class BloggerSiteProvider:
    api_version = 1
    site: BloggerSite

    def __init__(self, http: HttpClient | None = None) -> None:
        self.name = self.site.name
        self.priority = self.site.priority
        self._base = self.site.base
        self._http = http or HttpClient(
            headers={"User-Agent": AGENT, "Referer": f"{self._base}/"}
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- transport ---------------------------------------------------------- #
    async def _get(self, path: str, params: dict | None = None) -> str:
        try:
            return await self._http.get_text(f"{self._base}{path}", params=params)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"{self.name}: {e}") from e
        except HttpError as e:
            raise ProviderError(f"{self.name} request failed: {e}") from e

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        sources = await self.find_sources(anime, audio)
        return sources[0].ref() if sources else None

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        # Sub-only site: decline dub rather than serve subs mislabelled as dub.
        if audio is Audio.DUB:
            return []
        seen: dict[str, dict] = {}
        for term in search_terms(anime):
            query = _clean_query(term)
            if not query:
                continue
            html = await self._get("/", {"s": query})
            for item in parse_search(html, self.site.series_prefix):
                seen.setdefault(item["id"], item)
            if seen:
                break
        ranked = sorted(
            scored_matches(anime, list(seen.values())), key=lambda i: -i["_score"]
        )
        return [
            SourceOption(
                provider=self.name, anime_key=it["id"], title=it["title"],
                episode_count=None, audio=audio, confidence=it["_score"],
            )
            for it in ranked
        ]

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        html = await self._get(f"/{self.site.series_prefix}/{ref.anime_key}/")
        eps = [
            Episode(anime_id=anime_id, number=num, provider_ref=ref, episode_key=key)
            for num, key in parse_episodes(html)
        ]
        eps.sort(key=lambda e: e.number)
        return eps

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        html = await self._get(f"/{episode.episode_key}/")
        return [
            StreamCandidate(
                host="blogger",
                url=url,
                audio=episode.provider_ref.audio,
                headers={"Referer": f"{self._base}/"},
            )
            for url in parse_embed_iframes(html)
        ]


class AnimepaheChProvider(BloggerSiteProvider):
    site = ANIMEPAHE_CH


class ZorotvProvider(BloggerSiteProvider):
    site = ZOROTV


# --------------------------------------------------------------------------- #
# Pure HTML parsing (unit-tested without network)
# --------------------------------------------------------------------------- #
_ARTICLE_RE = re.compile(r"<article\b[\s\S]*?</article>")
_HREF_RE = re.compile(r'<a\b[^>]*?\bhref="([^"]+)"')
_TITLE_ATTR_RE = re.compile(r'\btitle="([^"]*)"')
_ITEM_RE = re.compile(
    r'<li\b[^>]*>\s*<a\b[^>]*?\bhref="([^"]+)"[^>]*>[\s\S]*?'
    r'class="epl-num">\s*([^<\s]+)\s*<'
)
_OPTION_RE = re.compile(r'<option\b[^>]*\bvalue="([^"]+)"')
_IFRAME_RE = re.compile(r"<iframe\b[^>]*?\bsrc=\"([^\"]+)\"", re.IGNORECASE)


def _clean_query(term: str) -> str:
    """Make a title searchable on WordPress' ``?s=``. These sites return nothing
    for "Frieren: Beyond Journey's End" but find it for "Frieren Beyond Journey
    s End", so punctuation (apostrophes included) becomes a space."""
    return " ".join(re.sub(r"[^\w\s]|_", " ", term).split())


def parse_search(html: str, series_prefix: str = "series") -> list[dict]:
    """Result cards → ``{id (series slug), title}``, site order preserved."""
    slug_re = re.compile(rf"/{re.escape(series_prefix)}/([^/?#\"]+)/?")
    out, seen = [], set()
    for card in _ARTICLE_RE.findall(html):
        href = _HREF_RE.search(card)
        m = slug_re.search(href.group(1)) if href else None
        if not m or m.group(1) in seen:
            continue
        title_m = _TITLE_ATTR_RE.search(card)
        title = html_lib.unescape(title_m.group(1)).strip() if title_m else m.group(1)
        seen.add(m.group(1))
        out.append({"id": m.group(1), "title": title})
    return out


def parse_episodes(html: str) -> list[tuple[float, str]]:
    """Series page → ``[(number, episode_key)]`` where the key is the episode
    page's path slug. Scoped to ``div.eplister`` so the "first/last episode"
    shortcuts above it and sidebar widgets can't leak in."""
    start = html.find('class="eplister"')
    region = html[start:] if start >= 0 else html
    end = region.find("</ul>")
    region = region[:end] if end >= 0 else region
    out: dict[float, str] = {}
    for href, num in _ITEM_RE.findall(region):
        n = to_float(num)
        key = href.split("://", 1)[-1].split("/", 1)[-1].strip("/")
        if n is None or not key:
            continue
        out.setdefault(n, key)
    return sorted(out.items())


def _is_blogger_embed(src: str) -> bool:
    low = src.lower()
    return "blogger.com/video.g" in low or (
        "/player/?" in low and "source=blogger" in low
    )


def _mirror_iframes(html: str) -> list[str]:
    """``<select class="mirror">`` options carry an alternative server as a
    base64'd ``<iframe …>`` snippet; decode them and return the iframe srcs."""
    out: list[str] = []
    for value in _OPTION_RE.findall(html):
        try:
            snippet = base64.b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
        except (binascii.Error, ValueError):
            continue
        out.extend(_IFRAME_RE.findall(snippet))
    return out


def parse_embed_iframes(html: str) -> list[str]:
    """Blogger-backed iframe URLs on an episode page, deduped, in order: either
    a raw ``blogger.com/video.g`` embed (animepahe.ch) or the site's
    ``/player/?source=blogger&url=…`` JW Player wrapper (zorotv.ba).

    The default player (``div.player-embed``) comes first, then any mirror
    servers from the server dropdown. Other hosts (megaplay, megavid …) are not
    ours to resolve and are skipped.
    """
    start = html.find("player-embed")
    srcs = _IFRAME_RE.findall(html[start:] if start >= 0 else html)
    srcs += _mirror_iframes(html)
    urls: list[str] = []
    for src in srcs:
        src = html_lib.unescape(src)
        if _is_blogger_embed(src) and src not in urls:
            urls.append(src)
    return urls
