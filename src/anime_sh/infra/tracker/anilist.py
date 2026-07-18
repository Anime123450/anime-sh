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
from urllib.parse import urlsplit

from ...domain.errors import MetadataError
from ...domain.models import Anime, AnimeId, WatchProgress
from ..http import HttpClient, HttpError
from ..metadata.anilist import API, _to_anime

_AUTHORIZE = "https://anilist.co/api/v2/oauth/authorize"

_VIEWER_Q = "query { Viewer { id name } }"

# Set progress (and status) on the authenticated user's list for one media.
_SAVE_M = """
mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
  SaveMediaListEntry(mediaId: $mediaId, progress: $progress, status: $status) {
    id progress status
  }
}
"""

# Pull the user's in-progress + completed list, newest activity first.
_LIST_Q = """
query ($userId: Int) {
  MediaListCollection(userId: $userId, type: ANIME,
      status_in: [CURRENT, COMPLETED, REPEATING, PAUSED]) {
    lists { entries {
      progress status updatedAt
      media {
        id idMal
        title { romaji english native }
        synonyms format status episodes season seasonYear genres
        description(asHtml: false) coverImage { large } duration
      }
    } }
  }
}
"""


def authorize_url(client_id: str) -> str:
    """The AniList implicit-grant URL to open in a browser. The token comes
    back in the redirect's URL fragment (``#access_token=…``)."""
    return f"{_AUTHORIZE}?client_id={client_id}&response_type=token"


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
        if self._viewer_id is None:
            await self.viewer()
        data = await self._query(_LIST_Q, {"userId": self._viewer_id})
        out: list[tuple[WatchProgress, Anime]] = []
        collection = (data.get("MediaListCollection") or {}).get("lists") or []
        for lst in collection:
            for entry in lst.get("entries") or []:
                media = entry.get("media") or {}
                if not media.get("id"):
                    continue
                anime = _to_anime(media)
                completed = entry.get("status") == "COMPLETED"
                updated = entry.get("updatedAt") or 0
                out.append(
                    (
                        WatchProgress(
                            anime_id=anime.id,
                            episode=float(entry.get("progress") or 0),
                            position_s=0,
                            duration_s=0,
                            updated_at=datetime.fromtimestamp(updated, tz=timezone.utc)
                            if updated
                            else datetime.now(timezone.utc),
                            completed=completed,
                        ),
                        anime,
                    )
                )
        return out
