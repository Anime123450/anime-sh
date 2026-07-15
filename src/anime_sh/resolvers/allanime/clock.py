"""Resolve AllAnime's internal ``/apivtwo/clock.json`` endpoint.

That endpoint returns a JSON list of links (mixed HLS/MP4, various resolutions,
sometimes with subtitle tracks). We map each into a domain :class:`Stream`; the
quality selector upstream picks among them.
"""

from __future__ import annotations

from ...domain.errors import ResolverError
from ...domain.models import Stream, StreamCandidate, Subtitle
from ...infra.http import HttpClient, HttpError
from ..quality import kind_from_url, quality_from_str

REFERER = "https://allmanga.to"


class AllAnimeClockResolver:
    name = "allanime-clock"
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(headers={"Referer": REFERER})

    def handles(self, candidate: StreamCandidate) -> bool:
        return "/apivtwo/clock" in candidate.url

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        try:
            data = await self._http.get_json(
                candidate.url, headers={"Referer": REFERER}
            )
        except HttpError as e:
            raise ResolverError(f"allanime clock failed: {e}") from e

        links = (data or {}).get("links") or []
        streams: list[Stream] = []
        for link in links:
            url = link.get("link") or link.get("hls") or link.get("mp4")
            if not url:
                continue
            streams.append(
                Stream(
                    url=url,
                    kind=kind_from_url(url),
                    quality=quality_from_str(link.get("resolutionStr")),
                    headers={"Referer": REFERER},
                    subtitles=_subtitles(link.get("subtitles")),
                )
            )
        return streams


def _subtitles(raw) -> tuple[Subtitle, ...]:
    if not raw:
        return ()
    out = []
    for s in raw:
        url = s.get("src") or s.get("url")
        if not url:
            continue
        out.append(
            Subtitle(
                url=url,
                lang=s.get("lang") or s.get("label") or "und",
                label=s.get("label"),
                default=bool(s.get("default")),
            )
        )
    return tuple(out)
