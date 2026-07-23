"""AllAnime rotating crypto material, derived from the live site.

The sources query is sealed with a key that rotates per site build and an
``epoch`` that rotates on a timer, and it is a server-side *persisted* query the
server only honours for its exact registered text. All of that is embedded in
the site's own JS bundle, so we derive it there — the same way the site does:

* ``epoch`` and ``partB`` come from ``window.__aaCrypto`` in the page HTML.
* The AES-256 ``key`` is ``mask XOR partB``, where ``mask`` is the lone 64-hex
  constant in the crypto chunk.
* The persisted source ``query`` (and thus its ``sha256`` hash) is reconstructed
  from the GraphQL template in that same chunk.

This is self-contained (no third-party key feed) and always self-consistent
across a build/epoch rotation. Any failure raises and the provider degrades to
``ProviderUnavailable``; the nightly canary tracks drift.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from ...infra.http import HttpClient, HttpError

SITE = "https://mkissa.to"
# The immutable JS bundle is served from this CDN; the exact base is confirmed
# against the page, with this as the fallback.
_CDN_IMMUTABLE = "https://cdn.allanime.day/all/mk/_app/immutable/"

# Refetch at most this often; epoch rotates every few days, so an hour is ample.
_TTL_S = 3600.0


@dataclass(frozen=True)
class Keygen:
    """Everything needed to sign the sources query and decrypt its reply."""

    epoch: int
    key: str  # hex-encoded AES-256 key
    query_hash: str
    query: str  # the exact persisted-query text (hashes to query_hash)
    fetched_at: float = field(default_factory=time.monotonic)

    @property
    def key_bytes(self) -> bytes:
        return bytes.fromhex(self.key)

    @property
    def stale(self) -> bool:
        return (time.monotonic() - self.fetched_at) > _TTL_S


# --------------------------------------------------------------------------- #
# Pure parsing (unit-tested without network)
# --------------------------------------------------------------------------- #
def parse_aacrypto(html: str) -> tuple[int, bytes]:
    """Extract ``(epoch, partB)`` from the page's ``window.__aaCrypto`` blob."""
    m = re.search(r"window\.__aaCrypto\s*=\s*(\{.*?\})", html)
    if not m:
        raise ValueError("no __aaCrypto in page")
    aa = json.loads(m.group(1))
    return int(aa["epoch"]), base64.b64decode(aa["partB"])


def derive_key(mask_hex: str, part_b: bytes) -> bytes:
    """AES key = ``mask XOR partB`` (per-byte, both 32 bytes)."""
    mask = bytes.fromhex(mask_hex)
    return bytes(a ^ b for a, b in zip(mask, part_b))


def extract_mask(chunk_js: str) -> str | None:
    """The crypto chunk carries exactly one 64-hex constant: the key mask."""
    masks = re.findall(r"[0-9a-f]{64}", chunk_js)
    return masks[0] if len(masks) == 1 else None


def resolve_source_query(chunk_js: str) -> str | None:
    """Reconstruct the ``episode(... sourceUrls ...)`` persisted query by
    resolving the JS template literal and its ``${...}`` interpolations."""
    template = next(
        (
            t
            for t in re.findall(r"(\nquery\([^`]*)`", chunk_js)
            if "sourceUrls" in t and "episode(" in t
        ),
        None,
    )
    if template is None:
        return None

    def resolve(tmpl: str, depth: int = 0) -> str:
        if depth > 6:
            return tmpl
        for name in re.findall(r"\$\{([^}]+)\}", tmpl):
            if name.endswith("()"):
                fn = re.search(
                    r"\b"
                    + re.escape(name[:-2])
                    + r"\s*=\s*\w+\s*=>\s*\w+\s*\?\s*`[^`]*`\s*:\s*`([^`]*)`",
                    chunk_js,
                )
                repl = fn.group(1) if fn else ""
            else:
                var = re.search(r"\b" + re.escape(name) + r"\s*=\s*`([^`]*)`", chunk_js)
                repl = resolve(var.group(1), depth + 1) if var else ""
            tmpl = tmpl.replace("${" + name + "}", repl)
        return tmpl

    query = resolve(template)
    return None if "${" in query else query


def _immutable_base(html: str) -> str:
    m = re.search(r"(https?://[^\"']+/_app/immutable/)", html)
    return m.group(1) if m else _CDN_IMMUTABLE


# --------------------------------------------------------------------------- #
# Network orchestration
# --------------------------------------------------------------------------- #
async def fetch_keygen(http: HttpClient) -> Keygen:
    """Derive current crypto material from the live site. Raises
    :class:`HttpError` on a network failure or a bundle we can't parse (both
    surfaced by the provider as ``ProviderUnavailable``)."""
    try:
        html = await http.get_text(f"{SITE}/")
        epoch, part_b = parse_aacrypto(html)

        base = _immutable_base(html)
        app_m = re.search(r"_app/immutable/(entry/app\.[^\"']+\.js)", html)
        if not app_m:
            raise ValueError("no app entry in page")
        app_js = await http.get_text(base + app_m.group(1))

        for chunk in re.findall(r"[\"']\.\./(chunks/[A-Za-z0-9_\-]+\.js)[\"']", app_js):
            chunk_js = await http.get_text(base + chunk)
            if "__aaCrypto" not in chunk_js:
                continue
            mask = extract_mask(chunk_js)
            query = resolve_source_query(chunk_js)
            if mask and query:
                key = derive_key(mask, part_b)
                return Keygen(
                    epoch=epoch,
                    key=key.hex(),
                    query_hash=hashlib.sha256(query.encode()).hexdigest(),
                    query=query,
                )
        raise ValueError("crypto chunk not found in bundle")
    except HttpError:
        raise
    except Exception as e:  # parse/format failures degrade gracefully
        raise HttpError(f"allanime keygen derivation failed: {e}") from e
