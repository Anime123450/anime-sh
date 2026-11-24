"""Decrypt megaplay-family (vidtube.site / megaplay.buzz / vidwish.live) ``getSources``.

Reverse-engineered from the embed page's ``lib/e1-player.min.js`` (2026-11-24).

Player algorithm (``decryptSourcesEnc`` + ``maybeAttachCdnToken`` + ``withAudioType``):

1. ``getSources`` returns ``{enc, t, server, intro, outro, tracks, image}``.
2. ``enc`` = URL-safe base64 (no padding) of AES-256-CBC ciphertext (PKCS7).
   * key = ``SOURCE_ENC_KEY`` UTF-8 bytes, zero-padded/truncated to 32 bytes
   * iv  = ``SOURCE_ENC_IV``  UTF-8 bytes, zero-padded/truncated to 16 bytes
   Plaintext is JSON ``{"file": "<master.m3u8 url>"}``.
3. For ``/master.m3u8`` URLs the player appends ``t=<sub|dub>`` (embed type).
4. CDN token: path key = ``<32hex>/<32hex>`` from the URL;
   ``msg = f"{now+90}|{pathkey}"``;
   ``token = b64url(msg) + "." + b64url(HMAC_SHA256(CDN_TOKEN_SECRET, msg))``
   appended as ``&token=``/``?token=`` (TTL 90 s, so resolve right before use).

HARD-CODED (static strings shipped in the player JS, identical in app.main.js):
``SOURCE_ENC_KEY``, ``SOURCE_ENC_IV``, ``CDN_TOKEN_SECRET``. If the site rotates
them, :func:`decrypt_sources` re-extracts them from the embed page's scripts
(``_discover_constants``) and retries.

Dependency: ``cryptography`` (AES-CBC only; HMAC/base64 are stdlib).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import quote, urlsplit

from ..flixcloud.crypto import (
    _aes_cbc_decrypt,  # cryptography if present, else pure Python
)

SOURCE_ENC_KEY = "i?LMTAx0Q6,:}50U"
SOURCE_ENC_IV = "W0;27ToaUpl_P%'c"
CDN_TOKEN_SECRET = "MpCdnT0k3n!9f2K#xQ7vL5mR8wN1pY4s"
CDN_TOKEN_TTL_SEC = 90

_PATHKEY_RE = re.compile(r"/([a-f0-9]{32})/([a-f0-9]{32})/", re.IGNORECASE)
_SCRIPT_RE = re.compile(r'<script[^>]+src="([^"]+)"', re.IGNORECASE)
_CONST_RES = {
    "key": re.compile(r'SOURCE_ENC_KEY\s*=\s*"((?:[^"\\]|\\.)*)"'),
    "iv": re.compile(r'SOURCE_ENC_IV\s*=\s*"((?:[^"\\]|\\.)*)"'),
    "secret": re.compile(r'CDN_TOKEN_SECRET\s*=\s*"((?:[^"\\]|\\.)*)"'),
}


class MegaplayDecryptError(Exception):
    pass


def _b64u_decode(s: str) -> bytes:
    s = s.strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _pad(s: str, n: int) -> bytes:
    return s.encode("utf-8")[:n].ljust(n, b"\0")


def _aes_decrypt(enc: str, key: str, iv: str) -> dict:
    raw = _b64u_decode(enc)
    if not raw or len(raw) % 16:
        raise MegaplayDecryptError("enc is not a whole number of AES blocks")
    pt = _aes_cbc_decrypt(_pad(key, 32), _pad(iv, 16), raw)
    n = pt[-1]
    if not 1 <= n <= 16 or pt[-n:] != bytes([n]) * n:
        raise MegaplayDecryptError("bad PKCS7 padding (wrong key/iv?)")
    obj = json.loads(pt[:-n].decode("utf-8"))
    if not isinstance(obj, dict) or not isinstance(obj.get("file"), str):
        raise MegaplayDecryptError("decrypted payload has no 'file'")
    return obj


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _audio_type(embed_url: str, page: str | None = None) -> str:
    # Embed URLs look like /stream/s-2/<id>/sub (or /dub, /hsub).
    last = urlsplit(embed_url).path.rstrip("/").rsplit("/", 1)[-1].lower()
    if last in ("sub", "dub", "hsub"):
        return "sub" if last == "hsub" else last
    if page:
        m = re.search(r"\btype\s*:\s*'(\w+)'", page)
        if m:
            t = m.group(1).lower()
            return "sub" if t == "hsub" else t
    return ""


def _discover_constants(embed_url: str, fetch) -> dict:
    """Re-extract key/iv/secret from the embed page's scripts (rotation fallback)."""
    status, page = fetch(embed_url, {"Referer": _origin(embed_url) + "/"})
    if status != 200:
        raise MegaplayDecryptError(f"embed page HTTP {status}")
    found: dict = {}
    for src in _SCRIPT_RE.findall(page):
        if "/lib/" not in src or "jquery" in src:
            continue
        st, js = fetch(src, {"Referer": embed_url})
        if st != 200:
            continue
        for name, rx in _CONST_RES.items():
            m = rx.search(js)
            if m and name not in found:
                found[name] = json.loads('"' + m.group(1) + '"')
        if len(found) == 3:
            break
    if "key" not in found or "iv" not in found:
        raise MegaplayDecryptError("could not locate SOURCE_ENC_KEY/IV in player JS")
    return found


def make_cdn_token(path_key: str, secret: str = CDN_TOKEN_SECRET, now: float | None = None) -> str:
    exp = int(now if now is not None else time.time()) + CDN_TOKEN_TTL_SEC
    msg = f"{exp}|{path_key}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return _b64u_encode(msg) + "." + _b64u_encode(sig)


def _with_audio_type(url: str, typ: str) -> str:
    if typ not in ("sub", "dub") or not re.search(r"/master\.m3u8(?:\?|$)", url, re.IGNORECASE):
        return url
    if re.search(r"[?&]t=", url):
        return url
    return url + ("&" if "?" in url else "?") + "t=" + quote(typ, safe="")


def attach_token(url: str, secret: str = CDN_TOKEN_SECRET, now: float | None = None) -> str:
    if re.search(r"[?&]token=", url):
        return url
    m = _PATHKEY_RE.search(url)
    if not m:
        return url  # player skips the token when no path key is present
    tok = make_cdn_token(f"{m.group(1).lower()}/{m.group(2).lower()}", secret, now)
    return url + ("&" if "?" in url else "?") + "token=" + quote(tok, safe="")


def decrypt_sources(data: dict, embed_url: str, fetch) -> dict:
    """``data``: parsed getSources JSON. ``fetch(url, headers) -> (status, text)``.

    Returns ``{'file': <tokenised m3u8 url>, 'headers': {...}}``. If ``data`` still
    carries a plain ``sources.file`` (old format) it is returned as-is.
    """
    origin = _origin(embed_url)
    headers = {"Referer": origin + "/", "Origin": origin}

    enc = data.get("enc")
    secret = CDN_TOKEN_SECRET
    page_type = None
    if enc:
        try:
            payload = _aes_decrypt(enc, SOURCE_ENC_KEY, SOURCE_ENC_IV)
        except (MegaplayDecryptError, ValueError, UnicodeDecodeError):
            c = _discover_constants(embed_url, fetch)
            secret = c.get("secret", secret)
            payload = _aes_decrypt(enc, c["key"], c["iv"])
        file_url = payload["file"]
    else:
        src = data.get("sources")
        file_url = (src.get("file") if isinstance(src, dict) else None) if src else None
        if not file_url:
            raise MegaplayDecryptError("getSources has neither 'enc' nor 'sources.file'")

    typ = _audio_type(embed_url, page_type)
    if not typ:
        st, page = fetch(embed_url, {"Referer": origin + "/"})
        typ = _audio_type(embed_url, page if st == 200 else None)
    file_url = attach_token(_with_audio_type(file_url, typ), secret)
    return {"file": file_url, "headers": headers}
