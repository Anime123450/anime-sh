"""AniList GraphQL metadata source — the identity spine.

Search, fetch, trending, seasonal, and airing schedule all come from here, so
the catalog works even when every streaming provider is down. Every returned
:class:`Anime` carries its AniList id, which is what providers later map onto.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timezone

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
from ..http import HttpClient, HttpError

API = "https://graphql.anilist.co"

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

_GET_Q = f"""
query ($id: Int, $malId: Int) {{
  Media(id: $id, idMal: $malId, type: ANIME) {{ {_MEDIA_FIELDS} }}
}}
"""

_TRENDING_Q = f"""
query ($perPage: Int) {{
  Page(perPage: $perPage) {{
    media(type: ANIME, sort: TRENDING_DESC) {{ {_MEDIA_FIELDS} }}
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

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _query(self, query: str, variables: dict) -> dict:
        try:
            data = await self._http.post_json(
                API, json={"query": query, "variables": variables}
            )
        except HttpError as e:
            raise MetadataError(f"AniList request failed: {e}") from e
        if "errors" in data:
            raise MetadataError(f"AniList error: {data['errors']}")
        return data["data"]

    async def search(self, query: str, *, limit: int = 20) -> list[Anime]:
        data = await self._query(_SEARCH_Q, {"search": query, "perPage": limit})
        return [_to_anime(m) for m in data["Page"]["media"]]

    async def get(self, id: AnimeId) -> Anime:
        # Omit missing ids entirely: an explicit {"malId": null} makes AniList
        # match Media(idMal: null) and 404 even when the AniList id is valid.
        variables = {
            k: v for k, v in (("id", id.anilist), ("malId", id.mal)) if v is not None
        }
        data = await self._query(_GET_Q, variables)
        media = data.get("Media")
        if not media:
            raise MetadataError(f"AniList has no media for {id.key}")
        return _to_anime(media)

    async def trending(self, *, limit: int = 30) -> list[Anime]:
        data = await self._query(_TRENDING_Q, {"perPage": limit})
        return [_to_anime(m) for m in data["Page"]["media"]]

    async def seasonal(self, season: Season, year: int) -> list[Anime]:
        data = await self._query(
            _SEASONAL_Q, {"season": season.value, "year": year, "perPage": 50}
        )
        return [_to_anime(m) for m in data["Page"]["media"]]

    async def airing_schedule(self, start: date, end: date) -> list[AiringEvent]:
        start_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp())
        data = await self._query(
            _SCHEDULE_Q, {"start": start_ts, "end": end_ts, "perPage": 50}
        )
        events = []
        for s in data["Page"]["airingSchedules"]:
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
