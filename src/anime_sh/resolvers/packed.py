"""Shared helper for "packed" HLS players (Filemoon, Streamwish, and mirrors).

These hosts hide the m3u8 inside Dean-Edwards ``eval(function(p,a,c,k,e,d){…})``
packed JavaScript. :func:`unpack` reverses that encoding; :func:`extract_hls`
pulls the master playlist URL out of the unpacked (or plain) source. Both are
pure and unit-tested, so a resolver just fetches the embed page and calls them.
"""

from __future__ import annotations

import re

# Dean-Edwards base alphabet (0-9, a-z, A-Z) — the encoder's digit order.
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

_PACKED_RE = re.compile(
    r"}\s*\(\s*'(?P<payload>.*?)'\s*,\s*(?P<radix>\d+)\s*,\s*(?P<count>\d+)\s*,"
    r"\s*'(?P<symtab>.*?)'\.split\('\|'\)",
    re.DOTALL,
)
_HLS_RE = re.compile(r'(?:file|source|src)\s*[:=]\s*["\'](?P<url>https?://[^"\']+?\.m3u8[^"\']*)', re.I)
_ANY_M3U8_RE = re.compile(r'["\'](?P<url>https?://[^"\']+?\.m3u8[^"\']*)', re.I)


def _unbase(token: str, radix: int) -> int:
    value = 0
    for ch in token:
        try:
            digit = _ALPHABET.index(ch)
        except ValueError:
            digit = 0
        value = value * radix + digit
    return value


def unpack(source: str) -> str:
    """Reverse one layer of p.a.c.k.e.r encoding. Returns ``source`` unchanged
    if it isn't packed (so callers can always run it defensively)."""
    m = _PACKED_RE.search(source)
    if not m:
        return source
    payload = m.group("payload").replace("\\'", "'").replace("\\\\", "\\")
    radix = int(m.group("radix"))
    symtab = m.group("symtab").split("|")

    def replace(match: re.Match) -> str:
        word = match.group(0)
        idx = _unbase(word, radix)
        if 0 <= idx < len(symtab) and symtab[idx]:
            return symtab[idx]
        return word

    return re.sub(r"\b\w+\b", replace, payload)


def extract_hls(source: str) -> str | None:
    """The m3u8 master URL from a player page — unpacking first if needed."""
    unpacked = unpack(source)
    for pattern in (_HLS_RE, _ANY_M3U8_RE):
        m = pattern.search(unpacked)
        if m:
            return m.group("url").replace("\\/", "/")
    return None
