"""AniList list-sync tracker — push/pull watch progress over GraphQL.

Auth is OAuth2 *implicit grant*: the user authorises a client they register once
(anilist.co/settings/developer) and pastes back the access token. anime-sh never
sees the user's password, only the token. See :func:`authorize_url` /
:func:`extract_token` for the browser handshake, and
:mod:`anime_sh.infra.tracker.tokens` for storage.

The tracker maps AniList's ``MediaList`` rows onto the domain
:class:`~anime_sh.domain.models.WatchProgress` (episode = list ``progress``), so
imported history flows through the same library as local playback.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from ...domain.errors import MetadataError
from ...domain.models import Anime, AnimeId, ListEntry, WatchProgress
from ..http import HttpClient, HttpError
from ..metadata.anilist import API, _to_anime

_AUTHORIZE = "https://anilist.co/api/v2/oauth/authorize"
_TOKEN = "https://anilist.co/api/v2/oauth/token"
# The out-of-band redirect AniList shows the code/token on, so no local server
# or custom redirect host is needed.
PIN_REDIRECT = "https://anilist.co/api/v2/oauth/pin"

_VIEWER_Q = "query { Viewer { id name } }"

# Set progress (and status) on the authenticated user's list for one media.
_SAVE_M = """
mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
  SaveMediaListEntry(mediaId: $mediaId, progress: $progress, status: $status) {
    id progress status
  }
}
"""

# Pull the user's whole anime list (every status), newest activity first.
_LIST_Q = """
query ($userId: Int) {
  MediaListCollection(userId: $userId, type: ANIME) {
    lists { entries {
      progress status updatedAt score(format: POINT_10_DECIMAL)
      media {
        id idMal
        title { romaji english native }
        synonyms format status episodes season seasonYear genres
        description(asHtml: false) coverImage { large } bannerImage duration
        averageScore popularity
        studios(isMain: true) { nodes { name isAnimationStudio } }
        nextAiringEpisode { episode airingAt }
      }
    } }
  }
}
"""

_SAVE_STATUS_M = """
mutation ($id: Int, $status: MediaListStatus) {
  SaveMediaListEntry(mediaId: $id, status: $status) { id status }
}
"""

_SAVE_SCORE_M = """
mutation ($id: Int, $scoreRaw: Int) {
  SaveMediaListEntry(mediaId: $id, scoreRaw: $scoreRaw) { id score }
}
"""

# Friendly status names ↔ AniList MediaListStatus.
STATUS_TO_ANILIST = {
    "watching": "CURRENT",
    "planning": "PLANNING",
    "completed": "COMPLETED",
    "paused": "PAUSED",
    "dropped": "DROPPED",
    "rewatching": "REPEATING",
}
STATUS_FROM_ANILIST = {v: k for k, v in STATUS_TO_ANILIST.items()}


def authorize_url(client_id: str, *, response_type: str = "code") -> str:
    """The AniList OAuth authorize URL to open in a browser.

    ``code`` (default) uses the auth-code + PIN flow: the redirect page shows a
    code the user pastes back, exchanged for a token via :func:`exchange_code`
    (needs the client secret). ``token`` uses implicit grant: the token itself
    comes back in the redirect fragment (no secret needed)."""
    params = {
        "client_id": client_id,
        "redirect_uri": PIN_REDIRECT,
        "response_type": response_type,
    }
    return f"{_AUTHORIZE}?{urlencode(params)}"


async def exchange_code(
    client_id: str, client_secret: str, code: str, http: HttpClient | None = None
) -> str:
    """Exchange an auth code (from the PIN page) for an access token.

    The client secret is used only here and never persisted."""
    own = http is None
    http = http or HttpClient(headers={"Accept": "application/json"})
    try:
        data = await http.post_json(
            _TOKEN,
            json={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": PIN_REDIRECT,
                "code": code.strip(),
            },
        )
    except HttpError as e:
        raise MetadataError(f"AniList token exchange failed: {e}") from e
    finally:
        if own:
            await http.aclose()
    token = (data or {}).get("access_token")
    if not token:
        raise MetadataError(f"AniList token exchange returned no token: {str(data)[:120]}")
    return token


def extract_token(pasted: str) -> str | None:
    """Pull the access token out of whatever the user pastes — the bare token,
    the full redirect URL, or just its ``#access_token=…`` fragment."""
    pasted = pasted.strip()
    if not pasted:
        return None
    m = re.search(r"access_token=([^&\s]+)", pasted)
    if m:
        return m.group(1)
    # A bare token: JWT-ish (three dot-separated segments), no spaces/URL bits.
    if " " not in pasted and "/" not in pasted and pasted.count(".") >= 2:
        return pasted
    return None


def _status_for(progress: int, total: int | None) -> str:
    return "COMPLETED" if total and progress >= total else "CURRENT"


class AniListTracker:
    """Implements the :class:`~anime_sh.domain.ports.Tracker` port for AniList."""

    name = "anilist"

    def __init__(self, token: str, http: HttpClient | None = None) -> None:
        self._token = token
        self._http = http or HttpClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self._viewer_id: int | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _query(self, query: str, variables: dict) -> dict:
        try:
            data = await self._http.post_json(
                API, json={"query": query, "variables": variables}
            )
        except HttpError as e:
            raise MetadataError(f"AniList sync request failed: {e}") from e
        if isinstance(data, dict) and data.get("errors"):
            raise MetadataError(f"AniList sync error: {data['errors']}")
        return data["data"]

    async def viewer(self) -> dict:
        """The authenticated user ``{id, name}`` — also validates the token."""
        data = await self._query(_VIEWER_Q, {})
        viewer = data.get("Viewer")
        if not viewer:
            raise MetadataError("AniList: token rejected (no viewer)")
        self._viewer_id = viewer["id"]
        return viewer

    async def push(self, progress: WatchProgress, *, total: int | None = None) -> None:
        """Set the user's AniList progress for one anime to ``progress.episode``.

        Marks the entry COMPLETED when the episode is the known finale, else
        CURRENT. Best-effort: only whole-numbered episodes with an AniList id
        are pushed (AniList counts integers)."""
        media_id = progress.anime_id.anilist
        if media_id is None:
            return
        ep = int(progress.episode)
        if ep <= 0:
            return
        await self._query(
            _SAVE_M,
            {"mediaId": media_id, "progress": ep, "status": _status_for(ep, total)},
        )

    async def pull(self) -> list[WatchProgress]:
        """The user's AniList list as domain progress rows (episode = list
        progress). :meth:`pull_with_media` also returns the show metadata."""
        return [wp for wp, _ in await self.pull_with_media()]

    async def pull_with_media(self) -> list[tuple[WatchProgress, Anime]]:
        out: list[tuple[WatchProgress, Anime]] = []
        for e in await self.fetch_list():
            out.append(
                (
                    WatchProgress(
                        anime_id=e.anime.id,
                        episode=float(e.progress),
                        position_s=0,
                        duration_s=0,
                        # A missing AniList updatedAt must sort *low*, not get
                        # stamped "now" — that made every sync bump such shows to
                        # the top and scramble Continue Watching's order.
                        updated_at=e.updated_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
                        # AniList ``progress = N`` means N episodes are finished,
                        # so the N-th is completed (the detail screen then treats
                        # every earlier episode as watched too). ``> 0`` guards
                        # planning entries with no progress.
                        completed=e.progress > 0,
                    ),
                    e.anime,
                )
            )
        return out

    async def fetch_list(self) -> list[ListEntry]:
        """The user's whole anime list as :class:`ListEntry` rows (status +
        score), most-recently-updated first."""
        if self._viewer_id is None:
            await self.viewer()
        data = await self._query(_LIST_Q, {"userId": self._viewer_id})
        entries: list[ListEntry] = []
        collection = (data.get("MediaListCollection") or {}).get("lists") or []
        for lst in collection:
            for entry in lst.get("entries") or []:
                media = entry.get("media") or {}
                if not media.get("id"):
                    continue
                updated = entry.get("updatedAt") or 0
                entries.append(
                    ListEntry(
                        anime=_to_anime(media),
                        status=entry.get("status") or "PLANNING",
                        progress=int(entry.get("progress") or 0),
                        score=float(entry.get("score") or 0),
                        updated_at=datetime.fromtimestamp(updated, tz=timezone.utc)
                        if updated
                        else None,
                    )
                )
        entries.sort(key=lambda e: e.updated_at or datetime.min.replace(tzinfo=timezone.utc),
                     reverse=True)
        return entries

    async def set_status(self, media_id: int, status: str) -> None:
        """Set the list status. ``status`` is a friendly name (watching/…)."""
        anilist_status = STATUS_TO_ANILIST.get(status.lower())
        if anilist_status is None:
            raise MetadataError(
                f"unknown status {status!r} (use: {', '.join(STATUS_TO_ANILIST)})"
            )
        await self._query(_SAVE_STATUS_M, {"id": media_id, "status": anilist_status})

    async def set_score(self, media_id: int, score: float) -> None:
        """Set the score (0–10). Stored as scoreRaw (0–100) so it is independent
        of the user's AniList display format."""
        if not 0 <= score <= 10:
            raise MetadataError("score must be between 0 and 10")
        await self._query(
            _SAVE_SCORE_M, {"id": media_id, "scoreRaw": round(score * 10)}
        )
