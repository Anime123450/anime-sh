"""Resolve Streamwish-family embeds (streamwish / embedwish / …) to HLS.

Streamwish and its many rotating mirrors serve a JWPlayer whose m3u8 is hidden
in packed JavaScript. We fetch the embed page with a browser UA + referer, unpack
it (:mod:`anime_sh.resolvers.packed`), and hand mpv the master playlist. Domains
rotate constantly, so matching leans on the provider's host *name* ("Sw") as much
as the URL.
"""

from __future__ import annotations

from ...domain.errors import ResolverError
from ...domain.models import Quality, Stream, StreamCandidate, StreamKind
from ...infra.http import HttpClient, HttpError
from ..packed import extract_hls

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

# Known Streamwish-family domain fragments + host-name hints (rotates often).
# Kept tight so this never clobbers the megaplay family (anikoto's Vidstream-*).
_DOMAINS = (
    "streamwish", "embedwish", "wishembed", "hlswish", "mwish", "awish",
    "dwish", "sfastwish", "wishfast",
)
_NAME_HINTS = ("sw", "wish", "streamwish")


class StreamwishResolver:
    name = "streamwish"
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        # Fail fast: these hosts are often geo/ISP-blocked, and a slow retry loop
        # would stall the resolve walk. One short attempt, then move on.
        self._http = http or HttpClient(headers={"User-Agent": AGENT}, timeout=8.0, retries=0)

    async def aclose(self) -> None:
        await self._http.aclose()

    def handles(self, candidate: StreamCandidate) -> bool:
        url = candidate.url.lower()
        if any(d in url for d in _DOMAINS):
            return True
        name = (candidate.host or "").lower()
        return any(name == h or name.startswith(h + "-") for h in _NAME_HINTS)

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        referer = candidate.headers.get("Referer") or candidate.url
        try:
            page = await self._http.get_text(
                candidate.url, headers={"Referer": referer}
            )
        except HttpError as e:
            raise ResolverError(f"streamwish fetch failed: {e}") from e
        url = extract_hls(page)
        if not url:
            raise ResolverError("streamwish: no m3u8 in embed page")
        return [
            Stream(
                url=url,
                kind=StreamKind.HLS,
                quality=Quality.UNKNOWN,
                headers={"Referer": _origin(candidate.url)},
            )
        ]


def _origin(url: str) -> str:
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"
