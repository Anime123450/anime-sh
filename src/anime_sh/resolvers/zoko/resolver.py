"""Resolve ZokoAnime embeds — hianime's one plaintext-HLS server.

The player page is a 1.6 KB shell whose whole configuration sits in a single
``window.__P="…"`` string: base64 of the config JSON XOR'd with a short
repeating key. Decoded, it hands over a plain HLS master playlist, ``.vtt``
subtitle tracks and intro/outro marks — no per-segment disguise, unlike the
megaplay family.

**The key is recovered, not hardcoded.** The plaintext is JSON that begins
``{"download_url":``, so XOR-ing the head of the payload against that known
prefix yields the key directly. A hardcoded key would be one silent site update
away from every episode failing; this way a rotation costs nothing, and the
JSON parse at the end is what says the recovery worked. The key seen in the
wild is tried first purely to save a few microseconds.

This is deliberately not the road AllAnime went down: that was a per-build AES
key plus a live-scraped timed nonce, which is why it was dropped. A repeating
XOR with a self-evident crib is a different order of thing.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re

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

log = logging.getLogger(__name__)

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"
_HOSTS = ("zokoanime.video",)
_PAYLOAD_RE = re.compile(r'window\.__P\s*=\s*"([A-Za-z0-9+/=]+)"')

# The config JSON's first key. Long enough to recover any key up to 17 bytes.
_CRIB = b'{"download_url"'
# What the site is using today; tried first, then the crib does the real work.
_KNOWN_KEY = b"otaku-embed-v1"
_MAX_KEY = len(_CRIB)


class ZokoResolver:
    name = "zoko"
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(headers={"User-Agent": AGENT})

    async def aclose(self) -> None:
        await self._http.aclose()

    def handles(self, candidate: StreamCandidate) -> bool:
        return any(h in candidate.url for h in _HOSTS)

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        try:
            page = await self._http.get_text(
                candidate.url, headers={"Referer": "https://hianime.at/"}
            )
        except HttpError as e:
            raise ResolverError(f"zoko embed fetch failed: {e}") from e

        found = _PAYLOAD_RE.search(page)
        if not found:
            raise ResolverError("zoko: no player payload in embed page")

        config = decode_payload(found.group(1))
        src = config.get("src")
        if not src:
            raise ResolverError("zoko: payload carries no stream URL")

        origin = candidate.url.split("/stream/", 1)[0]
        return [
            Stream(
                url=src,
                kind=StreamKind.HLS if ".m3u8" in src else StreamKind.MP4,
                quality=Quality.UNKNOWN,  # master playlist; mpv picks the variant
                headers={"Referer": f"{origin}/"},
                subtitles=_subtitles(config.get("subtitles")),
                skip_times=_skips(config.get("skip")),
            )
        ]


def decode_payload(encoded: str) -> dict:
    """Turn a ``window.__P`` string into the player's config dict.

    Pure and offline, so the key recovery below is unit-testable against a
    payload built with any key at all — which is the whole point of it.
    """
    try:
        blob = base64.b64decode(encoded + "==")
    except (binascii.Error, ValueError) as e:
        raise ResolverError(f"zoko: payload is not base64 ({e})") from e

    for key in _candidate_keys(blob):
        plain = bytes(b ^ key[i % len(key)] for i, b in enumerate(blob))
        try:
            config = json.loads(plain)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(config, dict):
            return config
    raise ResolverError("zoko: could not decode the player payload")


def _candidate_keys(blob: bytes) -> list[bytes]:
    """Keys to try, best first.

    Every key but the first is derived from the payload itself: XOR the head of
    the ciphertext against the known plaintext prefix and read off the key. The
    true length is unknown, so each length up to the crib's is offered and the
    JSON parse rejects the wrong ones.
    """
    head = bytes(a ^ b for a, b in zip(blob, _CRIB))
    keys = [_KNOWN_KEY]
    keys += [head[:length] for length in range(1, min(len(head), _MAX_KEY) + 1)]
    return keys


def _subtitles(tracks) -> tuple[Subtitle, ...]:
    if not tracks:
        return ()
    out = []
    for t in tracks:
        url = t.get("src") or t.get("file")
        if not url:
            continue
        out.append(
            Subtitle(
                url=url,
                lang=t.get("lang") or t.get("label") or "und",
                label=t.get("label"),
                default=bool(t.get("default")),
            )
        )
    return tuple(out)


def _skips(skip) -> SkipTimes | None:
    """``skip`` is null on most episodes; when present it mirrors megaplay's."""
    if not isinstance(skip, dict):
        return None

    def _range(obj):
        if not isinstance(obj, dict):
            return None
        start, end = obj.get("start", 0), obj.get("end", 0)
        return SkipRange(int(start), int(end)) if end and end > start else None

    op, ed = _range(skip.get("intro")), _range(skip.get("outro"))
    return SkipTimes(op=op, ed=ed) if (op or ed) else None
