"""Resolve mp4upload embed pages to a direct MP4.

ani-cli scrapes the ``player.src({... src: "…"})`` line from the embed HTML.
mp4upload serves the file directly, so the resolved stream is a plain MP4 that
mpv opens with the AllAnime referer.
"""

from __future__ import annotations

import re

from ...domain.errors import ResolverError
from ...domain.models import Stream, StreamCandidate, StreamKind, Quality
from ...infra.http import HttpClient, HttpError

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"
REFERER = "https://youtu-chan.com"

_SRC_RE = re.compile(r'src:\s*"([^"]+\.mp4[^"]*)"')
_ANY_MP4_RE = re.compile(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*')


class Mp4UploadResolver:
    name = "mp4upload"
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(headers={"User-Agent": AGENT})

    def handles(self, candidate: StreamCandidate) -> bool:
        return "mp4upload.com" in candidate.url

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        try:
            page = await self._http.get_text(
                candidate.url, headers={"Referer": REFERER}
            )
        except HttpError as e:
            raise ResolverError(f"mp4upload fetch failed: {e}") from e

        match = _SRC_RE.search(page) or _ANY_MP4_RE.search(page)
        if not match:
            raise ResolverError("mp4upload: no source in embed page")
        url = match.group(1) if match.re is _SRC_RE else match.group(0)
        return [
            Stream(
                url=url,
                kind=StreamKind.MP4,
                quality=Quality.UNKNOWN,
                headers={"Referer": "https://www.mp4upload.com/"},
            )
        ]
