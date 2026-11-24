"""Resolve flixcloud.cc embeds — reanime.cz's stream host.

The master URL is AES-encrypted behind a per-request WASM key mix and PBKDF2
(see :mod:`.crypto`); every playlist is then served as base64(XOR(text, pk)) and
segments are AES-128 HLS behind a referer check. We hand back the decrypted
master with ``playlist_key`` set, and the local proxy decodes playlists and
carries the referer for mpv.
"""

from __future__ import annotations

import asyncio

from ...domain.errors import ResolverError
from ...domain.models import Quality, Stream, StreamCandidate, StreamKind, Subtitle
from .._syncfetch import urllib_fetch
from . import crypto


class FlixcloudResolver:
    name = "flixcloud"
    api_version = 1

    def __init__(self, http=None) -> None:  # http unused: crypto drives its own fetch
        self._fetch = urllib_fetch

    async def aclose(self) -> None:
        return None

    def handles(self, candidate: StreamCandidate) -> bool:
        return "flixcloud." in candidate.url

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        try:
            info = await asyncio.to_thread(crypto.resolve, candidate.url, self._fetch)
        except crypto.FlixcloudError as e:
            raise ResolverError(f"flixcloud: {e}") from e
        except Exception as e:  # malformed page/WASM: treat as unresolvable
            raise ResolverError(f"flixcloud: decrypt failed ({type(e).__name__}: {e})") from e
        subs = tuple(
            Subtitle(url=s["url"], lang=(s.get("language") or "und"), default=bool(s.get("default")))
            for s in info.get("subtitles") or ()
            if s.get("url")
        )
        return [
            Stream(
                url=info["url"],
                kind=StreamKind.HLS,
                quality=Quality.UNKNOWN,
                headers=info["headers"],
                subtitles=subs,
                obfuscated=True,  # referer-gated + XOR playlists: must go via proxy
                playlist_key=info["playlist_key_b64"],
            )
        ]
