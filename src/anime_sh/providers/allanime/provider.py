"""AllAnime provider — protocol ported faithfully from ani-cli.

Reachability depends entirely on getting the request shape right: POST GraphQL
with Referer/Origin ``https://youtu-chan.com`` and a Firefox UA gets past the
Cloudflare edge that blocks naive clients. Episode sources come back inside an
AES-256-CTR ``tobeparsed`` blob; each source URL inside is then XOR-obfuscated.
Both are undone in :mod:`.decode`.

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
    Status,
    StreamCandidate,
)
from ...infra.http import CloudflareChallenge, HttpClient, HttpError
from .decode import (
    BUILD_ID,
    QUERY_HASH as _SOURCES_HASH,
    build_aa_req,
    decode_source_url,
    decrypt_tobeparsed,
)

log = logging.getLogger(__name__)

API = "https://api.allanime.day/api"
# ani-cli's headers — these are what actually clears the Cloudflare edge.
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

_SOURCES_GQL = (
    "query ($showId: String!, $translationType: VaildTranslationTypeEnumType!, "
    "$episodeString: String!) { episode( showId: $showId "
    "translationType: $translationType episodeString: $episodeString ) "
    "{ episodeString sourceUrls }}"
)


def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())


class AllAnimeProvider:
    name = "allanime"
    priority = 90
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(
            headers={"User-Agent": AGENT, "Referer": REFERER, "Origin": REFERER}
        )

    # -- transport helpers -------------------------------------------------- #
    async def _post(self, query: str, variables: dict, *, extensions: dict | None = None) -> dict:
        body = {"variables": variables, "query": query}
        headers = None
        if extensions is not None:
            body["extensions"] = extensions
            headers = {"x-build-id": str(BUILD_ID)}
        try:
            data = await self._http.post_json(API, json=body, headers=headers)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"allanime: {e}") from e
        except HttpError as e:
            raise ProviderError(f"allanime request failed: {e}") from e
        return _unwrap(data)

    async def _sources_payload(self, variables: dict) -> dict:
        """Fetch episode sources via the persisted-query GET, decrypting the
        ``tobeparsed`` blob. Falls back to a full POST query if needed."""
        try:
            raw = await self._http.get_json(
                API,
                params={
                    "variables": json.dumps(variables),
                    "extensions": json.dumps(
                        {
                            "persistedQuery": {"version": 1, "sha256Hash": _SOURCES_HASH},
                            # Anti-bot token; without it AllAnime returns
                            # AA_CRYPTO_MISSING and a null episode.
                            "aaReq": build_aa_req(),
                        }
                    ),
                },
                headers={"x-build-id": str(BUILD_ID)},
            )
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"allanime: {e}") from e
        except HttpError:
            raw = None

        data = (raw or {}).get("data") if isinstance(raw, dict) else None
        if isinstance(data, dict) and "tobeparsed" in data:
            return json.loads(decrypt_tobeparsed(data["tobeparsed"]).decode("utf-8", "replace"))
        if isinstance(data, dict) and data.get("episode"):
            return data

        # Fallback: full query over POST (also needs the aaReq token).
        data = await self._post(
            _SOURCES_GQL, variables, extensions={"aaReq": build_aa_req()}
        )
        return data

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
    planned total (or the most complete) wins.

    For a still-AIRING show the count-fit heuristic inverts (nothing can have
    the full total yet, so an entry that does is a different production) —
    there the closest title wins instead. See anikoto's ``_rank_items``."""
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
    releasing = anime.status is Status.RELEASING

    def rank(edge: dict) -> tuple:
        strong = edge["_score"] >= best_sim - 0.15
        eps = _avail(edge, translation)
        want = anime.episode_count
        if releasing:
            within = eps if strong and eps and (want is None or eps <= want) else 0
            return (-edge["_score"], -within)
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
