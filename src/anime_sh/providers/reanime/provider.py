"""Reanime provider (reanime.cz) — an AniList-keyed catalog over Flixcloud.

Reverse-engineered flow (2026-11-24):

1. ``GET /api/v1/search?q=…&limit=…``  → ``{results: [{anime_id: <slug>,
   anilist_id, title{english,romaji,native}, subbed, dubbed, episodes}]}``
2. ``GET /api/v1/anime/<slug>/episodes?limit=2000`` → ``{data: [{episode_number,
   subbed, dubbed, playable, title}]}`` (a numeric AniList id answers 400).
3. ``GET /api/flix/<anilist_id>/<episode>?mal=<mal>`` → ``{servers: [{serverName,
   dataType: sub|dub, dataLink: https://flixcloud.cc/e/<id>?v=N}]}``

Each Flixcloud embed is returned as a candidate; the ``flixcloud`` resolver
turns it into a playable stream. Because reanime is keyed by AniList id, a show
already identified by AniList matches exactly — no fuzzy title guessing —
which is what makes it a good fallback for titles the other providers lack.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

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

BASE = "https://reanime.cz"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"


class ReanimeProvider:
    name = "reanime"
    # Below hianime/anizone: every stream needs a Flixcloud decrypt round-trip,
    # so it's the fallback for shows the direct providers don't carry.
    priority = 60
    api_version = 1

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient(
            headers={"User-Agent": AGENT, "Referer": f"{BASE}/", "Accept": "application/json"}
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        try:
            data = await self._http.get_json(f"{BASE}{path}", params=params)
        except CloudflareChallenge as e:
            raise ProviderUnavailable(f"reanime: {e}") from e
        except HttpError as e:
            raise ProviderError(f"reanime request failed: {e}") from e
        if not isinstance(data, dict):
            raise ProviderError(f"reanime: unexpected response {str(data)[:120]}")
        return data

    # -- Provider port ------------------------------------------------------ #
    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        sources = await self.find_sources(anime, audio)
        return sources[0].ref() if sources else None

    async def find_sources(self, anime: Anime, audio: Audio) -> list[SourceOption]:
        seen: dict[str, dict] = {}
        exact: dict | None = None
        for query in search_terms(anime):
            data = await self._get_json("/api/v1/search", {"q": query, "limit": 20})
            items = parse_search(data)
            for it in items:
                if anime.id.anilist is not None and it["anilist"] == anime.id.anilist:
                    exact = {**it, "_score": 1.0}
            if exact:
                break
            for it in scored_matches(anime, items):
                seen.setdefault(it["id"], it)
            if seen:
                break

        ranked = [exact] if exact else rank_items(anime, list(seen.values()))
        count_key = "dub_eps" if audio is Audio.DUB else "sub_eps"
        ranked = [it for it in ranked if (it.get(count_key) or 0) > 0]
        return [
            SourceOption(
                provider=self.name, anime_key=it["id"], title=it["title"],
                episode_count=it.get(count_key), audio=audio, confidence=it["_score"],
            )
            for it in ranked
        ]

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        slug, anilist = split_key(ref.anime_key)
        data = await self._get_json(
            f"/api/v1/anime/{quote(slug, safe='')}/episodes", {"limit": 2000}
        )
        want_dub = ref.audio is Audio.DUB
        out: list[Episode] = []
        for ep in parse_episodes(data):
            if not (ep["dub"] if want_dub else ep["sub"]):
                continue
            mal = anime_id.mal or ""
            out.append(
                Episode(
                    anime_id=anime_id,
                    number=ep["num"],
                    provider_ref=ref,
                    # anilist|episode|mal — everything /api/flix needs.
                    episode_key=f"{anilist}|{ep['num']:g}|{mal}",
                    title=ep.get("title"),
                )
            )
        out.sort(key=lambda e: e.number)
        return out

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        anilist, num, mal = (episode.episode_key.split("|") + ["", "", ""])[:3]
        params = {"mal": mal} if mal else None
        data = await self._get_json(f"/api/flix/{anilist}/{num}", params)
        want = "dub" if episode.provider_ref.audio is Audio.DUB else "sub"
        out: list[StreamCandidate] = []
        seen: set[str] = set()
        for srv in parse_servers(data):
            if srv["type"] != want or srv["url"] in seen:
                continue
            seen.add(srv["url"])
            out.append(
                StreamCandidate(
                    host="flixcloud",
                    url=srv["url"],
                    audio=episode.provider_ref.audio,
                    headers={"Referer": f"{BASE}/"},
                )
            )
        return out


# --------------------------------------------------------------------------- #
# Pure JSON parsing (unit-tested without network)
# --------------------------------------------------------------------------- #
def split_key(anime_key: str) -> tuple[str, str]:
    """``anime_key`` is ``<slug>|<anilist_id>``."""
    slug, _, anilist = anime_key.partition("|")
    return slug, anilist


def parse_search(data: dict) -> list[dict]:
    items: list[dict] = []
    for r in data.get("results") or []:
        slug, anilist = r.get("anime_id"), r.get("anilist_id")
        if not slug or not anilist:
            continue
        t = r.get("title") or {}
        title = t.get("english") or t.get("romaji") or t.get("native") or slug
        items.append(
            {
                "id": f"{slug}|{anilist}",
                "anilist": int(anilist),
                "title": title,
                "jp": t.get("romaji") or "",
                "sub_eps": r.get("subbed"),
                "dub_eps": r.get("dubbed"),
                "episodes": r.get("episodes"),
            }
        )
    return items


def parse_episodes(data: dict) -> list[dict]:
    eps: list[dict] = []
    for e in data.get("data") or []:
        num = to_float(e.get("episode_number"))
        if num is None or e.get("playable") is False:
            continue
        eps.append(
            {"num": num, "sub": bool(e.get("subbed")), "dub": bool(e.get("dubbed")),
             "title": e.get("title") or None}
        )
    return eps


def parse_servers(data: dict) -> list[dict]:
    out: list[dict] = []
    for s in data.get("servers") or []:
        url, typ = s.get("dataLink"), s.get("dataType")
        if url and typ in ("sub", "dub"):
            out.append({"name": s.get("serverName") or "flixcloud", "type": typ, "url": url})
    return out
