"""AniList GraphQL metadata source — the identity spine.

Search, fetch, trending, seasonal, and airing schedule all come from here, so
the catalog works even when every streaming provider is down. Every returned
:class:`Anime` carries its AniList id, which is what providers later map onto.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ...domain.errors import MetadataError
from ...domain.models import (
    AiringEvent,
    Anime,
    AnimeId,
    Format,
    Season,
    Status,
    Title,
)
from ..http import HttpClient, HttpError, RateLimited

if TYPE_CHECKING:
    from ..cache.kv import KvCache

API = "https://graphql.anilist.co"

# How long each kind of catalog response stays fresh. Metadata drifts slowly and
# cache.db is disposable, so these are generous; airing data is kept shorter.
# Countdowns render from absolute timestamps, so a slightly stale schedule still
# counts down correctly.
_TTL_SEARCH = timedelta(minutes=15)
_TTL_MEDIA = timedelta(hours=1)
_TTL_TRENDING = timedelta(hours=1)
_TTL_SEASONAL = timedelta(hours=6)
_TTL_SCHEDULE = timedelta(minutes=30)
_TTL_SEQUEL = timedelta(hours=24)
_TTL_POPULAR = timedelta(hours=24)

_MEDIA_FIELDS = """
id idMal
title { romaji english native }
synonyms
format status episodes season seasonYear genres
description(asHtml: false)
coverImage { large }
bannerImage
duration
averageScore popularity
studios(isMain: true) { nodes { name isAnimationStudio } }
nextAiringEpisode { episode airingAt }
"""

_SEARCH_Q = f"""
query ($search: String, $perPage: Int) {{
  Page(perPage: $perPage) {{
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{ {_MEDIA_FIELDS} }}
  }}
}}
"""

_FILTER_Q = f"""
query ($search: String, $genres: [String], $year: Int, $format: MediaFormat,
       $status: MediaStatus, $sort: [MediaSort], $perPage: Int) {{
  Page(perPage: $perPage) {{
    media(search: $search, genre_in: $genres, seasonYear: $year, format: $format,
          status: $status, type: ANIME, sort: $sort) {{ {_MEDIA_FIELDS} }}
  }}
}}
"""

# Friendly sort names → AniList MediaSort enum.
_SORTS = {
    "popularity": "POPULARITY_DESC",
    "score": "SCORE_DESC",
    "trending": "TRENDING_DESC",
    "newest": "START_DATE_DESC",
    "title": "TITLE_ROMAJI",
}

_GET_Q = f"""
query ($id: Int, $malId: Int) {{
  Media(id: $id, idMal: $malId, type: ANIME) {{ {_MEDIA_FIELDS} }}
}}
"""

_RELATIONS_Q = f"""
query ($id: Int) {{
  Media(id: $id, type: ANIME) {{
    relations {{ edges {{ relationType node {{ type {_MEDIA_FIELDS} }} }} }}
  }}
}}
"""

_TRENDING_Q = f"""
query ($perPage: Int) {{
  Page(perPage: $perPage) {{
    media(type: ANIME, sort: TRENDING_DESC) {{ {_MEDIA_FIELDS} }}
  }}
}}
"""

_POPULAR_Q = f"""
query ($page: Int, $perPage: Int) {{
  Page(page: $page, perPage: $perPage) {{
    pageInfo {{ hasNextPage }}
    media(type: ANIME, sort: POPULARITY_DESC) {{ {_MEDIA_FIELDS} }}
  }}
}}
"""

_RECOMMENDATIONS_Q = f"""
query ($id: Int, $perPage: Int) {{
  Media(id: $id, type: ANIME) {{
    recommendations(sort: RATING_DESC, perPage: $perPage) {{
      nodes {{ mediaRecommendation {{ {_MEDIA_FIELDS} }} }}
    }}
  }}
}}
"""

_SEASONAL_Q = f"""
query ($season: MediaSeason, $year: Int, $perPage: Int) {{
  Page(perPage: $perPage) {{
    media(type: ANIME, season: $season, seasonYear: $year, sort: POPULARITY_DESC) {{
      {_MEDIA_FIELDS}
    }}
  }}
}}
"""

_SCHEDULE_Q = f"""
query ($start: Int, $end: Int, $perPage: Int) {{
  Page(perPage: $perPage) {{
    airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {{
      episode airingAt
      media {{ {_MEDIA_FIELDS} }}
    }}
  }}
}}
"""


def _clean_synopsis(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _to_anime(m: dict) -> Anime:
    # AniList occasionally returns a partial record. Without an id there is
    # no identity to hang anything off, and m["id"] used to raise a bare
    # KeyError straight out of search.
    if not m or m.get("id") is None:
        raise MetadataError("AniList returned a record with no id")
    t = m.get("title") or {}
    next_ep = m.get("nextAiringEpisode") or {}
    airing_at = next_ep.get("airingAt")
    return Anime(
        id=AnimeId(anilist=m["id"], mal=m.get("idMal")),
        title=Title(
            romaji=t.get("romaji"),
            english=t.get("english"),
            native=t.get("native"),
            synonyms=tuple(m.get("synonyms") or ()),
        ),
        format=_enum(Format, m.get("format")),
        status=_enum(Status, m.get("status")),
        episode_count=m.get("episodes"),
        season=_enum(Season, m.get("season")) if m.get("season") else None,
        year=m.get("seasonYear"),
        genres=tuple(m.get("genres") or ()),
        synopsis=_clean_synopsis(m.get("description")),
        cover_url=(m.get("coverImage") or {}).get("large"),
        duration_min=m.get("duration"),
        average_score=m.get("averageScore"),
        popularity=m.get("popularity"),
        studio=_main_studio(m.get("studios")),
        banner_url=m.get("bannerImage"),
        next_airing_episode=next_ep.get("episode"),
        next_airing_at=datetime.fromtimestamp(airing_at, tz=timezone.utc)
        if airing_at
        else None,
    )


def _anime_list(items) -> list[Anime]:
    """Map media rows, skipping any the API returned without an id.

    One malformed row shouldn't cost you the entire search result.
    """
    out: list[Anime] = []
    for m in items or ():
        try:
            out.append(_to_anime(m))
        except MetadataError:
            continue
    return out


def _main_studio(studios) -> str | None:
    nodes = (studios or {}).get("nodes") or []
    # Prefer an actual animation studio; fall back to the first main studio.
    animation = [n for n in nodes if n.get("isAnimationStudio")]
    pick = (animation or nodes)
    return pick[0].get("name") if pick else None


def _enum(enum_cls, value):
    if value is None:
        return enum_cls.UNKNOWN if hasattr(enum_cls, "UNKNOWN") else None
    try:
        return enum_cls(value)
    except ValueError:
        return enum_cls.UNKNOWN if hasattr(enum_cls, "UNKNOWN") else None


class AniListMetadata:
    name = "anilist"

    def __init__(
        self, http: HttpClient | None = None, *, cache: "KvCache | None" = None
    ) -> None:
        # Interactive: every call here backs a screen someone is looking at, so
        # cap how long a rate limit can stall it. Sitting out AniList's full
        # 60-second window would read as a frozen app.
        self._http = http or HttpClient(
            headers={"Accept": "application/json"}, max_retry_wait_s=5.0
        )
        self._cache = cache

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _query(self, query: str, variables: dict) -> dict:
        try:
            data = await self._http.post_json(
                API, json={"query": query, "variables": variables}
            )
        except RateLimited as e:
            # Actionable advice beats a URL and a status code: this one clears on
            # its own, and the client knows roughly when — so say so. The
            # exception already reads "rate limited — try again in about 41s";
            # prefixing it with "is rate-limiting requests" said it twice.
            raise MetadataError(f"AniList {e}") from e
        except HttpError as e:
            raise MetadataError(f"AniList request failed: {e}") from e
        if "errors" in data:
            raise MetadataError(f"AniList error: {data['errors']}")
        return data["data"]

    async def _cached(
        self,
        key: str,
        ttl: timedelta,
        produce: Callable[[], Awaitable[Any]],
        *,
        cache_if: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Return ``produce()``'s JSON-native result, served from cache.db when a
        fresh entry exists. A ``None`` result is never cached. No cache wired →
        ``produce`` runs every time (the pre-cache behaviour).

        ``cache_if`` lets a caller serve a degraded result without committing it
        to disk for the whole TTL — a half-fetched catalog is worth using for
        this session, and not worth being stuck with for a day.
        """
        if self._cache is not None:
            hit = await self._cache.get(key)
            if hit is not None:
                return hit
        value = await produce()
        worth_keeping = cache_if is None or cache_if(value)
        if self._cache is not None and value is not None and worth_keeping:
            await self._cache.set(key, value, ttl=ttl)
        return value

    async def search(self, query: str, *, limit: int = 20) -> list[Anime]:
        key = f"search:{limit}:{query.strip().lower()}"

        async def produce():
            data = await self._query(_SEARCH_Q, {"search": query, "perPage": limit})
            return data["Page"]["media"]

        media = await self._cached(key, _TTL_SEARCH, produce)
        return _anime_list(media)

    async def search_filtered(
        self,
        query: str | None = None,
        *,
        genres: list[str] | None = None,
        year: int | None = None,
        format: str | None = None,
        status: str | None = None,
        sort: str | None = None,
        limit: int = 20,
    ) -> list[Anime]:
        """Search/browse with filters. With no ``query`` this is a discovery
        browse (defaults to most-popular); with one it ranks by relevance unless
        a ``sort`` is given. ``sort`` is a friendly name (popularity/score/…)."""
        sort_enum = _SORTS.get((sort or "").lower()) or (
            "SEARCH_MATCH" if query else "POPULARITY_DESC"
        )
        variables = {
            "search": query,
            "genres": [g.title() for g in genres] if genres else None,
            "year": year,
            "format": format.upper() if format else None,
            "status": status.upper() if status else None,
            "sort": [sort_enum],
            "perPage": limit,
        }
        variables = {k: v for k, v in variables.items() if v is not None}
        key = "filter:" + json.dumps(variables, sort_keys=True)

        async def produce():
            data = await self._query(_FILTER_Q, variables)
            return data["Page"]["media"]

        media = await self._cached(key, _TTL_SEARCH, produce)
        return _anime_list(media)

    async def get(self, id: AnimeId) -> Anime:
        # Omit missing ids entirely: an explicit {"malId": null} makes AniList
        # match Media(idMal: null) and 404 even when the AniList id is valid.
        variables = {
            k: v for k, v in (("id", id.anilist), ("malId", id.mal)) if v is not None
        }

        async def produce():
            data = await self._query(_GET_Q, variables)
            return data.get("Media")  # None → not cached, raises below

        media = await self._cached(f"media:{id.key}", _TTL_MEDIA, produce)
        if not media:
            raise MetadataError(f"AniList has no media for {id.key}")
        return _to_anime(media)

    async def sequel(self, id: AnimeId) -> Anime | None:
        """The direct sequel (next season) of a show, or None. Only ANIME nodes
        with a SEQUEL relation are considered."""
        if id.anilist is None:
            return None

        async def produce():
            data = await self._query(_RELATIONS_Q, {"id": id.anilist})
            media = data.get("Media") or {}
            for edge in (media.get("relations") or {}).get("edges") or []:
                node = edge.get("node") or {}
                if edge.get("relationType") == "SEQUEL" and node.get("type") == "ANIME":
                    return node
            return None

        node = await self._cached(f"sequel:{id.key}", _TTL_SEQUEL, produce)
        return _to_anime(node) if node else None

    async def relations(self, id: AnimeId) -> list[tuple[str, Anime]]:
        """All related ANIME (prequels, sequels, side stories, movies…), each
        with its relation type. Non-anime relations (manga, novels) are skipped."""
        if id.anilist is None:
            return []

        async def produce():
            data = await self._query(_RELATIONS_Q, {"id": id.anilist})
            media = data.get("Media") or {}
            out = []
            for edge in (media.get("relations") or {}).get("edges") or []:
                node = edge.get("node") or {}
                if node.get("type") == "ANIME":
                    out.append({"relation": edge.get("relationType") or "RELATED",
                                "media": node})
            return out

        raw = await self._cached(f"relations:{id.key}", _TTL_SEQUEL, produce)
        return [(r["relation"], _to_anime(r["media"])) for r in raw]

    async def recommendations(self, id: AnimeId, *, limit: int = 15) -> list[Anime]:
        """Shows AniList users recommend for people who liked this one, best
        first. Empty when the show has no recommendations (or no AniList id)."""
        if id.anilist is None:
            return []

        async def produce():
            data = await self._query(
                _RECOMMENDATIONS_Q, {"id": id.anilist, "perPage": limit}
            )
            media = data.get("Media") or {}
            nodes = (media.get("recommendations") or {}).get("nodes") or []
            return [n["mediaRecommendation"] for n in nodes
                    if n.get("mediaRecommendation")]

        raw = await self._cached(f"recs:{id.key}:{limit}", _TTL_SEASONAL, produce)
        return _anime_list(raw)

    async def trending(self, *, limit: int = 30) -> list[Anime]:
        async def produce():
            data = await self._query(_TRENDING_Q, {"perPage": limit})
            return data["Page"]["media"]

        media = await self._cached(f"trending:{limit}", _TTL_TRENDING, produce)
        return _anime_list(media)

    async def popular(self, *, limit: int = 500) -> list[Anime]:
        """A broad, popularity-ranked catalog snapshot, most-popular first.

        SearchService turns this into a local index so search-as-you-type can do
        the substring / prefix / fuzzy matching AniList's strict word search
        can't (``fri`` -> *Frieren*, ``onepiece`` -> *One Piece*, ``the`` -> the
        popular shows whose title contains it). Disposable and day-cached; the
        pages are fetched in parallel so the one-time cold build is a single
        round-trip's worth of latency."""
        per = 50
        pages = max(1, -(-limit // per))  # ceil(limit / per)
        complete = True

        async def produce():
            nonlocal complete
            # One page failing must not discard the nine that worked. Ten pages
            # go out at once and AniList rate-limits below that, so losing one
            # was routine — and it threw away 450 usable titles, which then got
            # cached by the caller as "there is no index" for the rest of the
            # process. The pages are popularity-ordered, so what survives is the
            # part that matters most.
            results = await asyncio.gather(
                *(
                    self._query(_POPULAR_Q, {"page": p, "perPage": per})
                    for p in range(1, pages + 1)
                ),
                return_exceptions=True,
            )
            media: list[dict] = []
            failures = 0
            for data in results:
                if isinstance(data, BaseException):
                    failures += 1
                    continue
                media.extend(data["Page"]["media"])
            if not media:
                # Every page failed — that is a real failure, not a thin answer,
                # and the caller needs to know so it can retry later.
                first = next((r for r in results if isinstance(r, BaseException)), None)
                raise first or MetadataError("AniList returned no popular titles")
            complete = failures == 0
            return media[:limit]

        media = await self._cached(
            f"popular:{limit}", _TTL_POPULAR, produce,
            # Use a partial catalog now; don't be stuck with it for a day.
            cache_if=lambda _media: complete,
        )
        return _anime_list(media)

    async def seasonal(self, season: Season, year: int) -> list[Anime]:
        key = f"seasonal:{season.value}:{year}"

        async def produce():
            data = await self._query(
                _SEASONAL_Q, {"season": season.value, "year": year, "perPage": 50}
            )
            return data["Page"]["media"]

        media = await self._cached(key, _TTL_SEASONAL, produce)
        return _anime_list(media)

    async def airing_schedule(self, start: date, end: date) -> list[AiringEvent]:
        start_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp())
        key = f"schedule:{start_ts}:{end_ts}"

        async def produce():
            data = await self._query(
                _SCHEDULE_Q, {"start": start_ts, "end": end_ts, "perPage": 50}
            )
            return data["Page"]["airingSchedules"]

        schedules = await self._cached(key, _TTL_SCHEDULE, produce)
        events = []
        for s in schedules:
            if not s.get("media"):
                continue
            events.append(
                AiringEvent(
                    anime=_to_anime(s["media"]),
                    episode=float(s["episode"]),
                    airing_at=datetime.fromtimestamp(s["airingAt"], tz=timezone.utc),
                )
            )
        return events
