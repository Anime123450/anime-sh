"""Passthrough resolver for candidates that are already direct media URLs.

If a provider hands us a ``.m3u8``/``.mp4``/``.mpd`` link directly, there is
nothing to unwrap — mpv can open a master playlist and choose its own variant.
This is the catch-all that runs last after host-specific resolvers.
"""

from __future__ import annotations

from ...domain.models import Stream, StreamCandidate
from ..quality import kind_from_url, quality_from_str


class GenericStreamResolver:
    name = "generic"
    api_version = 1

    def handles(self, candidate: StreamCandidate) -> bool:
        url = candidate.url.split("?", 1)[0].lower()
        return url.endswith((".m3u8", ".mp4", ".mpd"))

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        return [
            Stream(
                url=candidate.url,
                kind=kind_from_url(candidate.url),
                quality=quality_from_str(candidate.quality_hint),
                headers=dict(candidate.headers),
                subtitles=tuple(candidate.subtitles),
            )
        ]
