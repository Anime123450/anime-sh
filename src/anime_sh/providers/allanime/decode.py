"""AllAnime response de-obfuscation, ported from ani-cli.

Two layers:

1. The GraphQL sources response wraps its payload in a ``tobeparsed`` blob,
   AES-256-CTR encrypted under ``sha256("Xot36i3lK3:v1")``. See
   :func:`decrypt_tobeparsed`.
2. Each individual ``sourceUrl`` is then obfuscated with a single-byte XOR
   cipher (``--`` prefix + hex byte-pairs, each byte XOR 0x38). See
   :func:`decode_source_url`. The full substitution table in ani-cli is exactly
   this XOR, verified pair-by-pair.
"""

from __future__ import annotations

import base64
import hashlib

_XOR_KEY = 0x38
# sha256("Xot36i3lK3:v1") — the AES-256 key ani-cli derives for `tobeparsed`.
_AES_KEY = hashlib.sha256(b"Xot36i3lK3:v1").digest()


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
