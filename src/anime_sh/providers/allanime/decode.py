"""AllAnime response de-obfuscation and request signing, ported from the site.

Three layers, all pure (no HTTP):

1. **Source-URL XOR.** Each ``sourceUrl`` is obfuscated with a single-byte XOR
   cipher (``--`` prefix + hex byte-pairs, each byte XOR ``0x38``). See
   :func:`decode_source_url`. Unchanged for years.
2. **``tobeparsed`` AES-256-GCM.** The sources response wraps its JSON payload in
   a base64 ``tobeparsed`` blob: ``0x01 || iv(12) || ciphertext || tag(16)``,
   AES-256-GCM. The key rotates per site build (fetched via keygen); the old
   static key ``sha256("Xot36i3lK3:v1")`` is kept as a fallback. See
   :func:`decrypt_tobeparsed`.
3. **``aaReq`` request token.** The sources query is now gated: the request must
   carry a short-lived AES-256-GCM token in ``extensions.aaReq`` or the API
   answers ``AA_CRYPTO_MISSING``. The token seals ``{v,ts,epoch,qh}`` under the
   same rotating key, with a deterministic per-request IV. See
   :func:`build_aareq`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_XOR_KEY = 0x38
# Legacy static key — still authenticates some ``tobeparsed`` responses, so it
# stays as a decrypt fallback behind the rotating per-build key.
_LEGACY_KEY = hashlib.sha256(b"Xot36i3lK3:v1").digest()

# The token/response IVs are pinned to a 5-minute window (``Math.floor(now/Cm)``
# in the site JS, ``Cm = 5 * 60_000``).
_TS_WINDOW_MS = 5 * 60_000


def decode_source_url(encoded: str) -> str:
    """Decode a ``--<hex>`` AllAnime source URL. Plain URLs pass through."""
    if not encoded.startswith("--"):
        return encoded
    body = encoded[2:]
    if len(body) % 2 != 0:
        return encoded
    try:
        raw = bytes(int(body[i : i + 2], 16) for i in range(0, len(body), 2))
    except ValueError:
        return encoded
    return "".join(chr(b ^ _XOR_KEY) for b in raw)


def encode_source_url(plain: str) -> str:
    """Inverse of :func:`decode_source_url`. For tests/fixtures."""
    return "--" + "".join(f"{ord(c) ^ _XOR_KEY:02x}" for c in plain)


def decrypt_tobeparsed(blob_b64: str, key: bytes | None = None) -> bytes:
    """Decrypt a base64 ``tobeparsed`` blob (AES-256-GCM).

    Layout: byte 0 is a version prefix, bytes 1..12 are the 12-byte IV, and the
    remainder is ``ciphertext || tag`` (the trailing 16 bytes are the GCM auth
    tag). The response may be sealed with either the current per-build ``key``
    or the legacy static key, so both are tried.
    """
    raw = base64.b64decode(blob_b64)
    iv, ct_and_tag = raw[1:13], raw[13:]
    last: Exception | None = None
    for candidate in (key, _LEGACY_KEY):
        if not candidate:
            continue
        try:
            return AESGCM(candidate).decrypt(iv, ct_and_tag, None)
        except Exception as e:  # InvalidTag et al. — try the next key
            last = e
    raise ValueError("tobeparsed could not be decrypted with any known key") from last


def encrypt_tobeparsed(
    plaintext: bytes, key: bytes | None = None, iv: bytes = b"123456789012"
) -> str:
    """Inverse of :func:`decrypt_tobeparsed` for tests. ``iv`` must be 12 bytes."""
    ct_and_tag = AESGCM(key or _LEGACY_KEY).encrypt(iv, plaintext, None)
    return base64.b64encode(b"\x01" + iv + ct_and_tag).decode()


def build_aareq(
    key: bytes, query_hash: str, epoch: int, *, now_ms: int | None = None
) -> str:
    """Build the ``extensions.aaReq`` token for the sources query.

    Mirrors the site's signer: seal ``{v,ts,epoch,qh}`` (compact JSON) with
    AES-256-GCM under ``key``, using ``iv = sha256("<epoch>:<qh>:<ts>")[:12]``,
    and emit ``base64(0x01 || iv || ciphertext || tag)``. ``ts`` is the current
    time floored to a 5-minute window.
    """
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    ts = ms // _TS_WINDOW_MS * _TS_WINDOW_MS
    payload = json.dumps(
        {"v": 1, "ts": ts, "epoch": epoch, "qh": query_hash}, separators=(",", ":")
    ).encode()
    iv = hashlib.sha256(f"{epoch}:{query_hash}:{ts}".encode()).digest()[:12]
    ct_and_tag = AESGCM(key).encrypt(iv, payload, None)
    return base64.b64encode(b"\x01" + iv + ct_and_tag).decode()
