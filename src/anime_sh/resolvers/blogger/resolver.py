"""Resolve Blogger video embeds (``blogger.com/video.g?token=…``) to direct MP4s.

Blogger's player page is plain HTML (no JS execution, no encryption needed): the
player config is embedded as an HTML-attribute-escaped JSON blob, and inside it
sit the ``googlevideo.com/videoplayback`` URLs for the progressive formats —
``itag=18`` (360p) and, when the upload has it, ``itag=22`` (720p). The URLs
carry several layers of escaping at once::

    &quot;https://rr1---sn-x.googlevideo.com/videoplayback?expire\\\\u003d1795…\\\\u0026ei\\\\u003d…

(``&quot;`` HTML entities, ``\\uXXXX`` JSON escapes, sometimes doubled
backslashes), so the extractor decodes both until it reaches a fixpoint.

Two entry points carry those URLs: the raw ``video.g`` embed, and the
"animestream-child" JW Player wrapper (``…/player/?source=blogger&url=<opaque>``)
some WordPress sites put in front of it, which renders the same
``googlevideo.com`` URLs into plain JS — so one extractor serves both.

The URLs expire within hours, so they are resolved at play time and never
cached. googlevideo also sniffs the user-agent: a non-browser UA gets a flat 403
(``text/plain``) even on a fresh URL, so every Stream carries a browser UA. A
request may also be 302-redirected to a closer cache node; players follow it.
"""

from __future__ import annotations

import html
import re

from ...domain.errors import ResolverError
from ...domain.models import Quality, Stream, StreamCandidate, StreamKind
from ...infra.http import HttpClient, HttpError

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

# YouTube-family progressive MP4 itags that Blogger serves. Anything else
# (DASH/WebM/SABR) is not a plain file mpv can range-read, so it is skipped.
ITAG_QUALITY: dict[int, Quality] = {
    18: Quality.Q360,
    22: Quality.Q720,
    37: Quality.Q1080,
    59: Quality.Q480,
}

# A videoplayback URL up to the first thing that can't be part of it: a quote
# (raw or ``&quot;``), a bare backslash that doesn't start a ``\uXXXX`` escape,
# whitespace, or a tag bracket. ``&amp;`` is allowed through (decoded later).
_URL_RE = re.compile(
    r"https?:(?:\\*/){2}[\w.-]*googlevideo\.com(?:\\*/)videoplayback\?"
    r"(?:\\+u[0-9a-fA-F]{4}|&amp;|&(?!quot;|#34;|#39;|apos;|lt;|gt;)|[^\\\"'<>\s&])*"
)
_UNICODE_ESC = re.compile(r"\\+u([0-9a-fA-F]{4})")
_ITAG_RE = re.compile(r"[?&]itag=(\d+)")


def _unescape(raw: str) -> str:
    """Undo HTML entities and ``\\uXXXX`` escapes (any number of layers)."""
    prev = None
    while prev != raw:
        prev = raw
        raw = html.unescape(raw)
        raw = _UNICODE_ESC.sub(lambda m: chr(int(m.group(1), 16)), raw)
        raw = raw.replace("\\/", "/")
    return raw


def parse_video_urls(page: str) -> dict[int, str]:
    """``{itag: url}`` for every progressive MP4 format on a Blogger player page.

    First URL per itag wins (the page repeats each one in several config blobs);
    URLs without an itag — the SABR/UMP streaming endpoint — are ignored.
    """
    found: dict[int, str] = {}
    for m in _URL_RE.finditer(page):
        url = _unescape(m.group(0))
        tag = _ITAG_RE.search(url)
        if not tag:
            continue
        found.setdefault(int(tag.group(1)), url)
    return found


def streams_from_page(page: str) -> list[Stream]:
    """Streams for the itags we know how to rank, best quality first."""
    streams = [
        Stream(url=url, kind=StreamKind.MP4, quality=ITAG_QUALITY[itag])
        for itag, url in parse_video_urls(page).items()
        if itag in ITAG_QUALITY
    ]
    order = {q: i for i, q in enumerate(
        (Quality.Q2160, Quality.Q1080, Quality.Q720, Quality.Q480, Quality.Q360))}
    streams.sort(key=lambda s: order.get(s.quality, 99))
    return streams


class BloggerResolver:
    name = "blogger"
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(
            headers={"User-Agent": AGENT}, timeout=10.0, retries=1
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def handles(self, candidate: StreamCandidate) -> bool:
        url = candidate.url.lower()
        return "blogger.com/video.g" in url or (
            "/player/?" in url and "source=blogger" in url
        )

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        try:
            page = await self._http.get_text(
                candidate.url, headers=dict(candidate.headers) or None
            )
        except HttpError as e:
            raise ResolverError(f"blogger fetch failed: {e}") from e
        streams = streams_from_page(page)
        if not streams:
            raise ResolverError("blogger: no playable formats (video removed?)")
        return [
            Stream(
                url=s.url, kind=s.kind, quality=s.quality,
                headers={"User-Agent": AGENT},
                subtitles=tuple(candidate.subtitles),
            )
            for s in streams
        ]
