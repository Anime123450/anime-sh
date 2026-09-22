"""HiAnime provider (hianime.at).

Same family as :mod:`anime_sh.providers.anikoto`, but a shorter road to a
stream — three requests instead of four, and the embed URL arrives base64'd in
the markup rather than behind another token exchange:

1. ``GET /search?keyword=…``                     → server-rendered ``flw-item``
   cards carrying ``data-id``, the title, and separate sub/dub episode counts.
2. ``GET /api/theme/episode/list/<id>``          → ``{"html": …}`` whose anchors
   carry ``data-number`` and the episode's own ``data-id``.
3. ``GET /api/theme/episode/servers?episodeId=`` → ``{"html": …}`` where each
   server has ``data-type`` (sub/dub) and ``data-hash``: base64 of the embed
   URL itself.

Note the routes are ``/api/theme/…``, not the ``/ajax/…`` the rest of the
family uses — those 404 here. They were read off the site's own player rather
than guessed at.

Sub and dub availability is per-server, not per-episode, so the episode listing
is audio-agnostic and the filter happens in :meth:`candidates`. Those servers
split two ways: ZokoAnime hands out a plaintext HLS master (see
``resolvers/zoko``), while the megaplay clones answer with an encrypted blob we
deliberately do not decrypt. Both are emitted and the resolvers decide — a
candidate nothing can resolve is the fan-out working as designed, not an error.
"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import logging
import re

from ...domain.errors import ProviderError, ProviderUnavailable
from ...domain.models import (
    Anime,
    AnimeId,
    Audio,
    Episode,
    ProviderRef,
    SourceOption,
    StreamCandidate,
)
from ...infra.http import CloudflareChallenge, HttpClient, HttpError
from .._matching import rank_items, scored_matches, search_terms, to_float

log = logging.getLogger(__name__)

BASE = "https://hianime.at"


def configured_base(default: str = BASE) -> str:
    """The host to talk to, from config, falling back to the built-in one.

    HiAnime runs behind a rotating set of mirrors, and which of them answer
    depends on where you are — from the machine this was written on, every
    mirror but one is blocked by the ISP, and that could as easily be the other
    way round for someone else. Waiting for a release to change a hostname is a
    poor answer to "this domain stopped working for me today".

    Deliberately not a hardcoded fallback list: an unreachable mirror costs a
    timeout on every search, and a list I cannot verify is a guess shipped as a
    feature. ``anime config set providers.hianime_base https://…`` is a fact the
    user actually has.
    """
    try:
        from ...config import load_config

        chosen = load_config().providers.hianime_base
    except Exception:
        # A broken config must not take the provider down with it; the default
        # host is right for almost everyone.
        return default
    return (chosen or default).rstrip("/")


AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"


class HianimeProvider:
    name = "hianime"
    # Below anikoto: the catalogue is deeper and the search more exact, but only
    # one of its servers resolves, so it succeeds less often per episode.
    priority = 80
    api_version = 1

    def __init__(self, http: HttpClient | None = None, *, base: str | None = None) -> None:
        # Resolved once per instance rather than read per request: the host must
        # not change underneath a search that is already walking its results.
        self._base = (base or configured_base()).rstrip("/")
        self._http = http or HttpClient(
            headers={
                "User-Agent": AGENT,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self._base}/",
            }
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- transport ---------------------------------------------------------- #
    async def _get_text(self, path: str, params: dict | None = None) -> str:
        try:
            return await self._http.get_text(f"{self._base}{path}", params=params)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"hianime: {e}") from e
        except HttpError as e:
            raise ProviderError(f"hianime request failed: {e}") from e

    async def _get_html_payload(self, path: str, params: dict | None = None) -> str:
        """These endpoints answer ``{"status": true, "html": "<markup>"}``."""
        try:
            data = await self._http.get_json(f"{self._base}{path}", params=params)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"hianime: {e}") from e
        except HttpError as e:
            raise ProviderError(f"hianime request failed: {e}") from e
        if not isinstance(data, dict):
            raise ProviderError(f"hianime: unexpected response {str(data)[:120]}")
        return data.get("html") or ""

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        sources = await self.find_sources(anime, audio)
        return sources[0].ref() if sources else None

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        want_dub = audio is Audio.DUB
        seen: dict[str, dict] = {}
        for query in search_terms(anime):
            html = await self._get_text("/search", {"keyword": query})
            for item in scored_matches(anime, parse_search(html)):
                seen.setdefault(item["id"], item)  # dedupe across query terms
            if seen:
                break

        out: list[SourceOption] = []
        for it in rank_items(anime, list(seen.values())):
            # The card states sub and dub counts separately, so an entry with no
            # dub at all can be dropped before it costs a round-trip.
            count = it.get("dub_eps") if want_dub else it.get("sub_eps")
            if want_dub and not count:
                continue
            out.append(
                SourceOption(
                    provider=self.name,
                    anime_key=it["id"],
                    title=it["title"],
                    episode_count=count,
                    audio=audio,
                    confidence=it["_score"],
                )
            )
        return out

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        html = await self._get_html_payload(f"/api/theme/episode/list/{ref.anime_key}")
        episodes = [
            Episode(
                anime_id=anime_id,
                number=ep["num"],
                provider_ref=ref,
                # Servers are keyed by the site's own episode id, not the number.
                episode_key=ep["ep_id"],
                title=ep["title"] or None,
            )
            for ep in parse_episodes(html)
        ]
        episodes.sort(key=lambda e: e.number)
        return episodes

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        ref = episode.provider_ref
        want = "dub" if ref.audio is Audio.DUB else "sub"
        html = await self._get_html_payload(
            "/api/theme/episode/servers", {"episodeId": episode.episode_key}
        )
        return [
            StreamCandidate(
                host=server["name"],
                url=server["url"],
                audio=ref.audio,
                headers={"Referer": f"{self._base}/"},
            )
            for server in parse_servers(html)
            if server["type"] == want
        ]


# --------------------------------------------------------------------------- #
# Pure HTML parsing (unit-tested without network)
# --------------------------------------------------------------------------- #
_CARD_SPLIT = '<div class="flw-item'
_ID_RE = re.compile(
    r'class="film-poster-ahref[^"]*"\s+title="([^"]*)"[^>]*?data-id="(\d+)"', re.S
)
_JNAME_RE = re.compile(r'data-jname="([^"]*)"')
_SUB_RE = re.compile(r'tick-item tick-sub"[^>]*>.*?(\d+)</div>', re.S)
_DUB_RE = re.compile(r'tick-item tick-dub"[^>]*>.*?(\d+)</div>', re.S)
_EP_RE = re.compile(
    r'<a\s+title="([^"]*)"[^>]*?data-number="([\d.]+)"\s+data-id="(\d+)"', re.S
)
_SERVER_RE = re.compile(
    r'data-type="(sub|dub|raw)"\s+data-server-name="([^"]+)"\s+data-hash="([^"]+)"'
)


def parse_search(html: str) -> list[dict]:
    """Parse result cards into {id, title, jp, sub_eps, dub_eps}."""
    items: list[dict] = []
    for chunk in html.split(_CARD_SPLIT)[1:]:
        found = _ID_RE.search(chunk)
        if not found:
            continue
        jp = _JNAME_RE.search(chunk)
        sub, dub = _SUB_RE.search(chunk), _DUB_RE.search(chunk)
        items.append(
            {
                "id": found.group(2),
                "title": html_lib.unescape(found.group(1)),
                "jp": html_lib.unescape(jp.group(1)) if jp else "",
                "sub_eps": int(sub.group(1)) if sub else None,
                "dub_eps": int(dub.group(1)) if dub else None,
            }
        )
    return items


def parse_episodes(html: str) -> list[dict]:
    """Parse the episode-list markup into {num, ep_id, title}."""
    eps: list[dict] = []
    for m in _EP_RE.finditer(html):
        num = to_float(m.group(2))
        if num is None:
            continue
        eps.append(
            {
                "num": num,
                "ep_id": m.group(3),
                "title": html_lib.unescape(m.group(1)).strip(),
            }
        )
    return eps


def parse_servers(html: str) -> list[dict]:
    """Parse the server markup into {type, name, url}.

    ``data-hash`` is base64 of the embed URL. A hash that does not decode is
    dropped rather than raised on: the other servers for the same episode are
    still perfectly good, and one malformed entry should not lose the episode.
    """
    servers: list[dict] = []
    for kind, name, hashed in _SERVER_RE.findall(html):
        url = _decode_hash(hashed)
        if not url:
            continue
        servers.append({"type": kind, "name": html_lib.unescape(name), "url": url})
    return servers


def _decode_hash(hashed: str) -> str | None:
    try:
        # Over-padding is harmless and saves computing the right amount.
        url = base64.b64decode(hashed + "==").decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        log.debug("hianime: undecodable server hash %r", hashed[:24])
        return None
    return url if url.startswith("http") else None
