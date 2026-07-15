"""AllAnime provider.

Maps an AniList identity to an AllAnime show, lists its episodes, and returns
ordered stream *candidates* (embed/clock URLs, not videos). Turning a candidate
into a playable stream is the resolver's job.

Note: AllAnime sits behind Cloudflare's managed challenge on some networks. When
that happens the shared HTTP client raises ``CloudflareChallenge``; we surface
it as ``ProviderUnavailable`` so the provider manager skips this source cleanly
rather than crashing. We do not attempt to defeat the challenge.
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
    StreamCandidate,
)
from ...infra.http import CloudflareChallenge, HttpClient, HttpError
from .decode import decode_source_url

log = logging.getLogger(__name__)

API = "https://api.allanime.day/api"
REFERER = "https://allmanga.to"
ORIGIN = "https://allmanga.to"

_SEARCH_GQL = (
    "query($search:SearchInput,$limit:Int,$page:Int,"
    "$translationType:VaildTranslationTypeEnumType,"
    "$countryOrigin:VaildCountryOriginEnumType){"
    "shows(search:$search,limit:$limit,page:$page,"
    "translationType:$translationType,countryOrigin:$countryOrigin){"
    "edges{_id name englishName availableEpisodes __typename}}}"
)

_EPISODES_GQL = (
    "query($showId:String!){show(_id:$showId){"
    "_id availableEpisodesDetail}}"
)

_SOURCES_GQL = (
    "query($showId:String!,$translationType:VaildTranslationTypeEnumType!,"
    "$episodeString:String!){episode(showId:$showId,"
    "translationType:$translationType,episodeString:$episodeString){"
    "episodeString sourceUrls}}"
)

# Prefer known-good internal/CDN hosts; unknowns still get tried, last.
_HOST_PRIORITY = {
    "Luf-mp4": 100,
    "Default": 90,
    "S-mp4": 80,
    "Sak": 70,
    "Kir": 60,
    "Yt-mp4": 50,
}


def _norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())


class AllAnimeProvider:
    name = "allanime"
    priority = 90
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(
            headers={"Referer": REFERER, "Origin": ORIGIN},
            impersonate="chrome",
        )

    # -- helpers ------------------------------------------------------------ #
    async def _gql(self, query: str, variables: dict) -> dict:
        try:
            data = await self._http.get_json(
                API, params={"variables": json.dumps(variables), "query": query}
            )
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"allanime: {e}") from e
        except HttpError as e:
            raise ProviderError(f"allanime request failed: {e}") from e
        if not isinstance(data, dict) or "data" not in data:
            raise ProviderError(f"allanime: unexpected response {str(data)[:120]}")
        return data["data"]

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        translation = "dub" if audio is Audio.DUB else "sub"
        candidates: list[dict] = []
        for query in _search_terms(anime):
            data = await self._gql(
                _SEARCH_GQL,
                {
                    "search": {
                        "allowAdult": False,
                        "allowUnknown": False,
                        "query": query,
                    },
                    "limit": 40,
                    "page": 1,
                    "translationType": translation,
                    "countryOrigin": "ALL",
                },
            )
            edges = (data.get("shows") or {}).get("edges") or []
            candidates.extend(edges)
            if edges:
                break

        best = _best_match(anime, candidates, translation)
        if best is None:
            return None
        return ProviderRef(
            provider=self.name,
            anime_key=best["_id"],
            audio=audio,
            confidence=best["_score"],
        )

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        data = await self._gql(_EPISODES_GQL, {"showId": ref.anime_key})
        show = data.get("show") or {}
        detail = show.get("availableEpisodesDetail") or {}
        key = "dub" if ref.audio is Audio.DUB else "sub"
        raw = detail.get(key) or []
        episodes: list[Episode] = []
        for ep_str in raw:
            num = _parse_ep_number(ep_str)
            if num is None:
                continue
            episodes.append(
                Episode(
                    anime_id=anime_id,
                    number=num,
                    provider_ref=ref,
                    episode_key=ep_str,
                )
            )
        episodes.sort(key=lambda e: e.number)
        return episodes

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        ref = episode.provider_ref
        translation = "dub" if ref.audio is Audio.DUB else "sub"
        data = await self._gql(
            _SOURCES_GQL,
            {
                "showId": ref.anime_key,
                "translationType": translation,
                "episodeString": episode.episode_key,
            },
        )
        ep = data.get("episode") or {}
        sources = ep.get("sourceUrls") or []
        out: list[StreamCandidate] = []
        for src in sources:
            url = decode_source_url(src.get("sourceUrl", ""))
            if not url:
                continue
            host = src.get("sourceName") or "unknown"
            out.append(
                StreamCandidate(
                    host=host,
                    url=_absolutize(url),
                    audio=ref.audio,
                    headers={"Referer": REFERER},
                    quality_hint=str(src.get("priority")) if src.get("priority") else None,
                )
            )
        out.sort(key=lambda c: -_HOST_PRIORITY.get(c.host, 0))
        return out


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested without network)
# --------------------------------------------------------------------------- #
def _search_terms(anime: Anime) -> list[str]:
    terms = [anime.title.romaji, anime.title.english, *anime.title.synonyms]
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out or ["".join(anime.title.preferred)]


def _best_match(anime: Anime, edges: list[dict], translation: str) -> dict | None:
    targets = [
        _norm(t)
        for t in (anime.title.romaji, anime.title.english, *anime.title.synonyms)
        if t
    ]
    best, best_score = None, 0.0
    for edge in edges:
        names = [edge.get("name"), edge.get("englishName")]
        score = max(
            (
                SequenceMatcher(None, _norm(n), tgt).ratio()
                for n in names
                if n
                for tgt in targets
            ),
            default=0.0,
        )
        # Nudge toward shows whose episode count roughly matches metadata.
        avail = (edge.get("availableEpisodes") or {}).get(translation)
        if anime.episode_count and avail and abs(avail - anime.episode_count) <= 1:
            score += 0.05
        if score > best_score:
            best, best_score = edge, score
    if best is None or best_score < 0.6:
        return None
    best = dict(best)
    best["_score"] = round(best_score, 3)
    return best


def _parse_ep_number(ep_str: str) -> float | None:
    try:
        return float(ep_str)
    except (TypeError, ValueError):
        return None


def _absolutize(url: str) -> str:
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        # AllAnime's clock endpoint wants the .json variant.
        url = url.replace("/clock?", "/clock.json?")
        return f"https://api.allanime.day{url}"
    return url
