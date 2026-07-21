"""AllAnime provider — protocol ported faithfully from the current site.

Reachability depends entirely on getting the request shape right. The API
(``api.allanime.day``) sits behind a Cloudflare edge cleared by a Firefox UA
plus the site's own ``Origin``/``Referer``. AllAnime rebranded to
``mkissa.to`` and now binds its crypto to that origin — sending the old
``youtu-chan.com`` origin makes the sources query fail with ``AA_CRYPTO_STALE``.

Episode sources are additionally gated: the query must carry a short-lived
``aaReq`` AES-256-GCM token (built with a per-build key + rotating ``epoch``,
tracked by :mod:`.keygen`) or the API answers ``AA_CRYPTO_MISSING``. The reply
comes back inside an AES-256-GCM ``tobeparsed`` blob; each source URL inside is
then XOR-obfuscated. All three layers are undone in :mod:`.decode`.

The provider returns stream *candidates* (embed URLs per host); resolvers turn
those into playable streams. When AllAnime is unreachable the provider degrades
to ``ProviderUnavailable`` and the manager skips it.
"""

from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher

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
from .decode import build_aareq, decode_source_url, decrypt_tobeparsed
from .keygen import Keygen, fetch_keygen

log = logging.getLogger(__name__)

API = "https://api.allanime.day/api"
# The current site origin. The API binds its source-crypto to this origin, and a
# Firefox UA clears the Cloudflare edge.
SITE = "https://mkissa.to"
# Playback referer for the resolved CDN/clock streams (unchanged; see the clock
# resolver). This is *not* the API origin.
REFERER = "https://youtu-chan.com"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

_SEARCH_GQL = (
    "query( $search: SearchInput $limit: Int $page: Int "
    "$translationType: VaildTranslationTypeEnumType "
    "$countryOrigin: VaildCountryOriginEnumType ) { shows( search: $search "
    "limit: $limit page: $page translationType: $translationType "
    "countryOrigin: $countryOrigin ) { edges { _id name availableEpisodes "
    "airedStart __typename } }}"
)

_EPISODES_GQL = (
    "query ($showId: String!) { show( _id: $showId ) "
    "{ _id availableEpisodesDetail }}"
)

# The sources query is a server-side *persisted* query addressed only by its
# rotating hash (see :mod:`.keygen`); there is no client-sent query string.


def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())


class AllAnimeProvider:
    name = "allanime"
    priority = 90
    api_version = 1

    def __init__(
        self, http: HttpClient | None = None, keygen: Keygen | None = None
    ) -> None:
        self._http = http or HttpClient(
            headers={"User-Agent": AGENT, "Referer": f"{SITE}/", "Origin": SITE}
        )
        # An injected keygen pins the crypto (tests); otherwise it is fetched and
        # cached lazily on first source request.
        self._keygen = keygen

    # -- transport helpers -------------------------------------------------- #
    async def _post(self, query: str, variables: dict) -> dict:
        try:
            data = await self._http.post_json(
                API, json={"variables": variables, "query": query}
            )
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"allanime: {e}") from e
        except HttpError as e:
            raise ProviderError(f"allanime request failed: {e}") from e
        return _unwrap(data)

    async def _get_keygen(self) -> Keygen:
        if self._keygen is None or self._keygen.stale:
            try:
                self._keygen = await fetch_keygen(self._http)
            except CloudflareChallenge as e:
                raise ProviderUnavailable(f"allanime: {e}") from e
            except HttpError as e:
                raise ProviderUnavailable(f"allanime: keygen unavailable: {e}") from e
        return self._keygen

    async def _sources_payload(self, variables: dict) -> dict:
        """Fetch episode sources via the ``aaReq``-signed persisted-query GET and
        decrypt the ``tobeparsed`` blob. Returns ``{}`` when the API declines to
        return sources (e.g. an uncached persisted query or crypto rotation)."""
        keygen = await self._get_keygen()
        key = keygen.key_bytes
        aareq = build_aareq(key, keygen.query_hash, keygen.epoch)
        try:
            # Send the full persisted-query text (APQ register): the server only
            # keeps the hash warm intermittently, so hash-only alone returns
            # ``PersistedQueryNotFound`` on a cold instance.
            raw = await self._http.get_json(
                API,
                params={
                    "query": keygen.query,
                    "variables": json.dumps(variables),
                    "extensions": json.dumps(
                        {
                            "persistedQuery": {
                                "version": 1,
                                "sha256Hash": keygen.query_hash,
                            },
                            "aaReq": aareq,
                        }
                    ),
                },
            )
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"allanime: {e}") from e
        except HttpError as e:
            raise ProviderError(f"allanime sources request failed: {e}") from e

        data = raw.get("data") if isinstance(raw, dict) else None
        if isinstance(data, dict) and data.get("tobeparsed"):
            plain = decrypt_tobeparsed(data["tobeparsed"], key)
            return json.loads(plain.decode("utf-8", "replace"))
        if isinstance(data, dict) and data.get("episode"):
            return data
        # GraphQL errors (PersistedQueryNotFound / AA_CRYPTO_*): no usable sources.
        if isinstance(raw, dict) and raw.get("errors"):
            log.debug("allanime sources: %s", raw["errors"])
        return {}

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        sources = await self.find_sources(anime, audio)
        return sources[0].ref() if sources else None

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        translation = "dub" if audio is Audio.DUB else "sub"
        edges: list[dict] = []
        for query in _search_terms(anime):
            data = await self._post(
                _SEARCH_GQL,
                {
                    "search": {"allowAdult": False, "allowUnknown": False, "query": query},
                    "limit": 40,
                    "page": 1,
                    "translationType": translation,
                    "countryOrigin": "ALL",
                },
            )
            edges = (data.get("shows") or {}).get("edges") or []
            if edges:
                break

        return [
            SourceOption(
                provider=self.name, anime_key=e["_id"], title=e.get("name") or "?",
                episode_count=_avail(e, translation), audio=audio,
                confidence=e["_score"],
            )
            for e in _scored_edges(anime, edges, translation)
        ]

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        data = await self._post(_EPISODES_GQL, {"showId": ref.anime_key})
        detail = (data.get("show") or {}).get("availableEpisodesDetail") or {}
        key = "dub" if ref.audio is Audio.DUB else "sub"
        episodes: list[Episode] = []
        for ep_str in detail.get(key) or []:
            num = _parse_ep_number(ep_str)
            if num is None:
                continue
            episodes.append(
                Episode(anime_id=anime_id, number=num, provider_ref=ref, episode_key=ep_str)
            )
        episodes.sort(key=lambda e: e.number)
        return episodes

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        ref = episode.provider_ref
        translation = "dub" if ref.audio is Audio.DUB else "sub"
        payload = await self._sources_payload(
            {
                "showId": ref.anime_key,
                "translationType": translation,
                "episodeString": episode.episode_key,
            }
        )
        sources = (payload.get("episode") or payload).get("sourceUrls") or []
        out: list[StreamCandidate] = []
        for src in sources:
            url = decode_source_url(src.get("sourceUrl", ""))
            if not url:
                continue
            out.append(
                StreamCandidate(
                    host=src.get("sourceName") or "unknown",
                    url=_absolutize(url),
                    audio=ref.audio,
                    headers={"Referer": REFERER},
                    quality_hint=_priority_str(src.get("priority")),
                )
            )
        # Higher provider-reported priority first.
        out.sort(key=lambda c: -_priority_val(c.quality_hint))
        return out


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without network)
# --------------------------------------------------------------------------- #
def _unwrap(data) -> dict:
    if not isinstance(data, dict) or "data" not in data:
        raise ProviderError(f"allanime: unexpected response {str(data)[:120]}")
    return data["data"]


def _search_terms(anime: Anime) -> list[str]:
    terms = [anime.title.romaji, anime.title.english, *anime.title.synonyms]
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out or [anime.title.preferred]


def _avail(edge: dict, translation: str) -> int | None:
    avail = edge.get("availableEpisodes")
    if isinstance(avail, dict):
        return avail.get(translation)
    return None


# Fuzzy gate: keep near-matches so alternate/variant entries also surface.
_MATCH_THRESHOLD = 0.55


def _scored_edges(anime: Anime, edges: list[dict], translation: str) -> list[dict]:
    """Edges whose title fuzzily matches, tagged with ``_score`` and ranked
    best-first — among close titles, the one whose episode count best fits the
    planned total (or the most complete) wins."""
    targets = [
        _norm(t)
        for t in (anime.title.romaji, anime.title.english, *anime.title.synonyms)
        if t
    ]
    scored: list[dict] = []
    for edge in edges:
        name = edge.get("name")
        if not name:
            continue
        sim = max((SequenceMatcher(None, _norm(name), tgt).ratio() for tgt in targets), default=0.0)
        if sim < _MATCH_THRESHOLD:
            continue
        edge = dict(edge)
        edge["_score"] = round(sim, 3)
        scored.append(edge)
    if not scored:
        return []
    best_sim = max(e["_score"] for e in scored)

    def rank(edge: dict) -> tuple:
        strong = edge["_score"] >= best_sim - 0.15
        eps = _avail(edge, translation)
        want = anime.episode_count
        if strong and want and eps:
            ep_key = abs(eps - want)
        elif strong and eps:
            ep_key = -eps
        else:
            ep_key = 10_000
        return (ep_key, -edge["_score"])

    return sorted(scored, key=rank)


def _best_match(anime: Anime, edges: list[dict], translation: str) -> dict | None:
    ranked = _scored_edges(anime, edges, translation)
    return ranked[0] if ranked else None


def _parse_ep_number(ep_str: str) -> float | None:
    try:
        return float(ep_str)
    except (TypeError, ValueError):
        return None


def _absolutize(url: str) -> str:
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        # AllAnime's internal clock endpoint; base is allanime.day (not the api
        # host), and it wants the .json variant.
        url = url.replace("/clock?", "/clock.json?")
        return f"https://allanime.day{url}"
    return url


def _priority_str(value) -> str | None:
    return str(value) if value is not None else None


def _priority_val(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except ValueError:
        return 0.0
