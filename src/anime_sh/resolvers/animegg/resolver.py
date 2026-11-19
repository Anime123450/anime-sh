"""Resolve an animegg.org embed page to direct MP4 streams (one per quality).

The embed page carries a plaintext JWPlayer ``videoSources`` array::

    var videoSources = [{file: "/play/550902/video.mp4?for=…", label: "480p", …}, …];

Each ``/play/…`` URL embeds a token minted for this fetch, and 302s to a
``vidcache.net`` CDN only when the request carries ``Referer: <the embed URL>``
(without it the site answers 500). So every Stream carries that Referer, which
mpv/VLC then send on the redirect hop too.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from ...domain.errors import ResolverError
from ...domain.models import Quality, Stream, StreamCandidate, StreamKind
from ...infra.http import HttpClient, HttpError
from ..quality import quality_from_str

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"
_EMBED_MARK = "animegg.org/embed/"

_SOURCES_RE = re.compile(r"videoSources\s*=\s*\[(.*?)\]\s*;", re.DOTALL)
_ENTRY_RE = re.compile(
    r"\{[^{}]*?file:\s*[\"'](?P<file>[^\"']+)[\"'][^{}]*?label:\s*[\"'](?P<label>[^\"']*)[\"'][^{}]*?\}",
    re.DOTALL,
)


class AnimeggResolver:
    name = "animegg"
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        # Fail fast on a dead host so the resolve walk moves on quickly.
        self._http = http or HttpClient(
            headers={"User-Agent": AGENT}, timeout=10.0, retries=1
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def handles(self, candidate: StreamCandidate) -> bool:
        return _EMBED_MARK in candidate.url

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        referer = next(
            (v for k, v in candidate.headers.items() if k.lower() == "referer"),
            "https://www.animegg.org/",
        )
        try:
            page = await self._http.get_text(
                candidate.url, headers={"Referer": referer}
            )
        except HttpError as e:
            raise ResolverError(f"animegg embed fetch failed: {e}") from e

        streams = parse_sources(page, candidate.url)
        if not streams:
            raise ResolverError("animegg: no videoSources in embed page")
        return streams


def parse_sources(page: str, embed_url: str) -> list[Stream]:
    """Embed HTML → MP4 streams, best quality first."""
    block = _SOURCES_RE.search(page)
    if not block:
        return []
    headers = {"Referer": embed_url, "User-Agent": AGENT}
    streams: list[Stream] = []
    for entry in _ENTRY_RE.finditer(block.group(1)):
        streams.append(
            Stream(
                url=urljoin(embed_url, entry.group("file")),
                kind=StreamKind.MP4,
                quality=quality_from_str(entry.group("label")),
                headers=dict(headers),
            )
        )
    streams.sort(key=_quality_rank)
    return streams


_ORDER = [Quality.Q2160, Quality.Q1080, Quality.Q720, Quality.Q480, Quality.Q360]


def _quality_rank(stream: Stream) -> int:
    return _ORDER.index(stream.quality) if stream.quality in _ORDER else len(_ORDER)
