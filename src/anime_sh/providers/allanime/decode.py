"""AllAnime obfuscates its internal source URLs with a single-byte XOR cipher.

Each obfuscated URL is a ``--`` prefix followed by hex byte-pairs; every byte
is XORed with 0x38. This is a pure function so it is unit-tested directly with a
round-trip, independent of the (frequently-changing) live API.
"""

from __future__ import annotations

_XOR_KEY = 0x38


def decode_source_url(encoded: str) -> str:
    """Decode a ``--<hex>`` AllAnime source URL. Non-encoded input is returned
    unchanged (some sources are already plain URLs)."""
    if not encoded.startswith("--"):
        return encoded
    body = encoded[2:]
    if len(body) % 2 != 0:
        return encoded  # malformed; leave it for the caller to skip
    try:
        raw = bytes(int(body[i : i + 2], 16) for i in range(0, len(body), 2))
    except ValueError:
        return encoded
    return "".join(chr(b ^ _XOR_KEY) for b in raw)


def encode_source_url(plain: str) -> str:
    """Inverse of :func:`decode_source_url`. Exists for tests/fixtures."""
    return "--" + "".join(f"{ord(c) ^ _XOR_KEY:02x}" for c in plain)
