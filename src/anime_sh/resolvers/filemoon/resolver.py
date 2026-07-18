"""Resolve Filemoon-family embeds to HLS.

Filemoon (and its rotating mirrors) serves a packed-JS JWPlayer, sometimes behind
one extra iframe hop. We fetch the embed page with a browser UA + referer, follow
a single ``/e/`` iframe if the m3u8 isn't inline, unpack the script
(:mod:`anime_sh.resolvers.packed`) and return the master playlist.
"""

from __future__ import annotations

import re

from ...domain.errors import ResolverError
from ...domain.models import Quality, Stream, StreamCandidate, StreamKind
from ...infra.http import HttpClient, HttpError
from ..packed import extract_hls

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

# Filemoon-family domain fragments + host-name hints (rotates often).
_DOMAINS = ("filemoon", "moonplayer", "moviesm4u", "bysekoze", "kerapoxy", "furher")
_NAME_HINTS = ("fm", "moon", "filemoon", "fm-hls")
_IFRAME_RE = re.compile(r'<iframe[^>]+src="([^"]+)"', re.I)


class FilemoonResolver:
    name = "filemoon"
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(headers={"User-Agent": AGENT})

    def handles(self, candidate: StreamCandidate) -> bool:
        url = candidate.url.lower()
        if any(d in url for d in _DOMAINS):
            return True
        name = (candidate.host or "").lower()
        return any(name == h or name.startswith(h + "-") for h in _NAME_HINTS)

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        referer = candidate.headers.get("Referer") or candidate.url
        page = await self._get(candidate.url, referer)
        url = extract_hls(page)
        if not url:
            # Filemoon often nests the real player one iframe deep.
            m = _IFRAME_RE.search(page)
            if m:
                inner = m.group(1)
                if inner.startswith("//"):
                    inner = "https:" + inner
                page = await self._get(inner, candidate.url)
                url = extract_hls(page)
        if not url:
            raise ResolverError("filemoon: no m3u8 in embed page")
        return [
            Stream(
                url=url,
                kind=StreamKind.HLS,
                quality=Quality.UNKNOWN,
                headers={"Referer": _origin(candidate.url)},
            )
        ]

    async def _get(self, url: str, referer: str) -> str:
        try:
            return await self._http.get_text(url, headers={"Referer": referer})
        except HttpError as e:
            raise ResolverError(f"filemoon fetch failed: {e}") from e


def _origin(url: str) -> str:
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"
