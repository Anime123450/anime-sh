"""Resolve megaplay-family embeds — anikoto's stream hosts.

Covers the interchangeable megaplay clones (vidtube.site, megaplay.buzz,
vidwish.live, …): the file id is in the embed URL path (or, failing that, in the
page as ``cidu``/``data-id``), and ``<origin>/stream/getSources?id=<id>`` returns
the m3u8, subtitle tracks, and intro/outro skip times in plain JSON (no
encryption). We map that into a domain :class:`Stream`.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ...domain.errors import ResolverError
from ...domain.models import (
    Quality,
    SkipRange,
    SkipTimes,
    Stream,
    StreamCandidate,
    StreamKind,
    Subtitle,
)
from ...infra.http import HttpClient, HttpError

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

# Interchangeable megaplay-clone hosts anikoto rotates between.
_FAMILY_HOSTS = ("vidtube.site", "megaplay.buzz", "vidwish.live")
# The player's own file id, from the embed page (NOT the numeric id in the URL,
# which is anikoto's and differs from the megaplay host's). ``data-id`` is the
# real file id; ``cidu`` is a per-request nonce — getSources answers it with the
# same decoy stream for every episode, so it is only a last-resort fallback.
_CIDU_RE = re.compile(r"cidu\s*:\s*'([^']+)'")
_DATAID_RE = re.compile(r'data-id="(\d+)"')
# getSources is AJAX-only and 403s without this header.
_XHR = {"X-Requested-With": "XMLHttpRequest"}


class VidtubeResolver:
    name = "megaplay"
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(headers={"User-Agent": AGENT})

    async def aclose(self) -> None:
        await self._http.aclose()

    def handles(self, candidate: StreamCandidate) -> bool:
        return any(h in candidate.url for h in _FAMILY_HOSTS)

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        origin = _origin(candidate.url)
        file_id = await self._file_id(candidate.url)

        try:
            data = await self._http.get_json(
                f"{origin}/stream/getSources",
                params={"id": file_id},
                headers={"Referer": f"{origin}/", **_XHR},
            )
        except HttpError as e:
            raise ResolverError(f"megaplay getSources failed: {e}") from e

        file_url = ((data or {}).get("sources") or {}).get("file")
        if not file_url:
            # Measured 02/10/2026: megaplay stopped serving the playlist in the
            # clear. getSources now answers `{enc, t, server, intro, outro,
            # tracks}` — no `sources` at all — where `enc` is 128 bytes of
            # URL-safe base64, an exact multiple of the block size, and no
            # repeating-XOR key recovers any plausible JSON prefix from it. The
            # key is not in the payload.
            #
            # That is the AllAnime road, which this project does not take: the
            # zoko resolver's docstring recovers a key *from a crib in the
            # payload* and says why a per-build key plus a live nonce was dropped
            # rather than chased. So the candidate stays unresolvable and the
            # fan-out moves on to another provider.
            #
            # Named separately because "returned no file" reads as an empty or
            # failed response, and sent one investigation looking for exactly
            # that. The shape is intact; it is the contents that are shut.
            if (data or {}).get("enc"):
                raise ResolverError(
                    "megaplay: the playlist is encrypted (`enc`) rather than "
                    "served in the clear, and the key is not in the payload — "
                    "no installed resolver can open it"
                )
            raise ResolverError("megaplay: getSources returned no file")

        return [
            Stream(
                url=file_url,
                kind=StreamKind.HLS if ".m3u8" in file_url else StreamKind.MP4,
                quality=Quality.UNKNOWN,  # master playlist; mpv picks the variant
                headers={"Referer": f"{origin}/"},
                subtitles=_subtitles(data.get("tracks")),
                skip_times=_skips(data),
                # The megaplay family serves PNG-disguised segments off a CDN
                # whose hostname rotates (nekostream → kotocdn → …). Flag it
                # here so the proxy never has to guess from the host.
                obfuscated=True,
            )
        ]

    async def _file_id(self, url: str) -> str:
        """The megaplay file id comes from the embed page (``data-id``), not the
        URL — the numeric URL segment is anikoto's id and does not match."""
        try:
            page = await self._http.get_text(
                url, headers={"Referer": "https://anikototv.to/"}
            )
        except HttpError as e:
            raise ResolverError(f"megaplay embed fetch failed: {e}") from e
        pm = _DATAID_RE.search(page) or _CIDU_RE.search(page)
        if not pm:
            raise ResolverError("megaplay: no file id in embed page")
        return pm.group(1)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _subtitles(tracks) -> tuple[Subtitle, ...]:
    if not tracks:
        return ()
    out = []
    for t in tracks:
        if t.get("kind") not in (None, "captions", "subtitles"):
            continue
        url = t.get("file")
        if not url:
            continue
        out.append(
            Subtitle(
                url=url,
                lang=t.get("label") or "und",
                label=t.get("label"),
                default=bool(t.get("default")),
            )
        )
    return tuple(out)


def _skips(data) -> SkipTimes | None:
    def _range(obj):
        if not obj:
            return None
        start, end = obj.get("start", 0), obj.get("end", 0)
        return SkipRange(int(start), int(end)) if end and end > start else None

    op, ed = _range(data.get("intro")), _range(data.get("outro"))
    return SkipTimes(op=op, ed=ed) if (op or ed) else None
