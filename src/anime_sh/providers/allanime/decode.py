"""AllAnime response de-obfuscation + request signing, ported from ani-cli.

Three layers:

1. The episode-sources request must carry an ``aaReq`` anti-bot token — an
   AES-256-GCM signature of a small timestamped payload. Missing/invalid tokens
   get ``AA_CRYPTO_MISSING`` and a null episode. See :func:`build_aa_req`.
2. The GraphQL sources response wraps its payload in a ``tobeparsed`` blob,
   AES-256-CTR encrypted under :data:`_AES_KEY`. See :func:`decrypt_tobeparsed`.
3. Each individual ``sourceUrl`` is then obfuscated with a single-byte XOR
   cipher (``--`` prefix + hex byte-pairs, each byte XOR 0x38). See
   :func:`decode_source_url`. The full substitution table in ani-cli is exactly
   this XOR, verified pair-by-pair.

The key/epoch/build-id below track ani-cli's constants (v4.15.0). When AllAnime
rotates them and every candidate list goes empty again, these are what to bump —
they live in ani-cli's MAIN block (``allanime_key`` / ``allanime_epoch`` /
``allanime_build_id`` / ``allanime_query_hash``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

_XOR_KEY = 0x38
# AllAnime's shared secret (ani-cli v4.15.0). Used both as the AES-256-GCM key
# for the aaReq request token and the AES-256-CTR key for the tobeparsed
# response. Was sha256("Xot36i3lK3:v1") before AllAnime's 2026-07 crypto change.
_AES_KEY = bytes.fromhex(
    "cf4777b5778aeadc9449e12769ea545d00c43cd8ff65d482364586cde204f359"
)
# Persisted-query hash for the episode-sources query; also signed into aaReq.
QUERY_HASH = "d405d0edd690624b66baba3068e0edc3ac90f1597d898a1ec8db4e5c43c00fec"
# aaReq payload constants (ani-cli MAIN block).
_EPOCH = 4130
BUILD_ID = 41


def build_aa_req(now: float | None = None) -> str:
    """Build the ``aaReq`` anti-bot token for the episode-sources request.

    Mirrors ani-cli's ``get_aa_req`` (v4.15.0): a 5-minute-rounded millisecond
    timestamp is signed into a JSON payload, AES-256-GCM encrypted under
    :data:`_AES_KEY` with a 12-byte nonce = ``sha256(iv_payload)[:12]``. The
    token is ``base64('\\x01' || nonce || ciphertext || tag)``.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    ts = int((int(now if now is not None else time.time()) // 300) * 300 * 1000)
    iv_payload = f"{_EPOCH}:{BUILD_ID}:{QUERY_HASH}:{ts}"
    nonce = hashlib.sha256(iv_payload.encode()).digest()[:12]
    payload = json.dumps(
        {"v": 1, "ts": ts, "epoch": _EPOCH, "buildId": str(BUILD_ID), "qh": QUERY_HASH},
        separators=(",", ":"),
    ).encode()
    # AESGCM.encrypt returns ciphertext||tag, matching ani-cli's layout.
    ct_and_tag = AESGCM(_AES_KEY).encrypt(nonce, payload, None)
    return base64.b64encode(b"\x01" + nonce + ct_and_tag).decode()


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


def decrypt_tobeparsed(blob_b64: str) -> bytes:
    """Decrypt a base64 ``tobeparsed`` blob (AES-256-CTR).

    Layout (from ani-cli's ``process_response``): byte 0 is a prefix, bytes
    1..12 are the 12-byte IV, the counter is ``IV || 00000002``, and the last
    16 bytes (an unused auth tag) are dropped. CTR needs no padding.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    raw = base64.b64decode(blob_b64)
    counter = raw[1:13] + bytes.fromhex("00000002")
    ciphertext = raw[13 : len(raw) - 16]
    decryptor = Cipher(algorithms.AES(_AES_KEY), modes.CTR(counter)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def encrypt_tobeparsed(plaintext: bytes, iv: bytes = b"123456789012") -> str:
    """Inverse of :func:`decrypt_tobeparsed` for tests. ``iv`` must be 12 bytes."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    counter = iv + bytes.fromhex("00000002")
    encryptor = Cipher(algorithms.AES(_AES_KEY), modes.CTR(counter)).encryptor()
    ct = encryptor.update(plaintext) + encryptor.finalize()
    blob = b"\x00" + iv + ct + b"\x00" * 16
    return base64.b64encode(blob).decode()
