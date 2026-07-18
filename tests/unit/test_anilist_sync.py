"""AniList tracker + SyncService — offline via a fake HTTP client / fake tracker."""

from __future__ import annotations

from datetime import datetime, timezone

from anime_sh.app.sync import SyncService
from anime_sh.domain.models import Anime, AnimeId, Title, WatchProgress
from anime_sh.infra.tracker.anilist import (
    AniListTracker,
    authorize_url,
    exchange_code,
    extract_token,
)

from .fakes import FakeLibrary


# -- token handshake helpers ------------------------------------------------- #
def test_authorize_url_defaults_to_code_flow():
    url = authorize_url("12345")
    assert "client_id=12345" in url and "response_type=code" in url
    assert "oauth%2Fpin" in url or "oauth/pin" in url


def test_authorize_url_implicit_variant():
    url = authorize_url("12345", response_type="token")
    assert "response_type=token" in url


async def test_exchange_code_posts_and_returns_token():
    http = _FakeHttp([{"access_token": "T.O.K", "token_type": "Bearer"}])
    tok = await exchange_code("46250", "sekret", "the-code", http=http)
    assert tok == "T.O.K"
    sent = http.calls[0]
    assert sent["grant_type"] == "authorization_code"
    assert sent["client_id"] == "46250" and sent["client_secret"] == "sekret"
    assert sent["code"] == "the-code"


def test_extract_token_from_various_inputs():
    tok = "aaa.bbb.ccc"
    assert extract_token(tok) == tok
    assert extract_token(f"https://anilist.co/api/v2/oauth/pin#access_token={tok}&token_type=Bearer") == tok
    assert extract_token(f"  access_token={tok}  ") == tok
    assert extract_token("") is None
    assert extract_token("not a token with spaces") is None


# -- tracker over a fake HTTP ------------------------------------------------ #
class _FakeHttp:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def post_json(self, url, *, json=None, headers=None):
        self.calls.append(json)
        return self._responses.pop(0)

    async def aclose(self):
        pass


async def test_push_sets_progress_and_completed_status():
    http = _FakeHttp([{"data": {"SaveMediaListEntry": {"id": 1, "progress": 12, "status": "COMPLETED"}}}])
    tracker = AniListTracker("tok", http=http)
    await tracker.push(
        WatchProgress(AnimeId(anilist=196187), 12.0, 0, 0, datetime.now(timezone.utc), completed=True),
        total=12,
    )
    vars_ = http.calls[0]["variables"]
    assert vars_ == {"mediaId": 196187, "progress": 12, "status": "COMPLETED"}


async def test_push_uses_current_status_mid_season():
    http = _FakeHttp([{"data": {"SaveMediaListEntry": {"id": 1}}}])
    tracker = AniListTracker("tok", http=http)
    await tracker.push(
        WatchProgress(AnimeId(anilist=196187), 3.0, 0, 0, datetime.now(timezone.utc)),
        total=12,
    )
    assert http.calls[0]["variables"]["status"] == "CURRENT"


async def test_push_skips_when_no_anilist_id():
    http = _FakeHttp([])
    tracker = AniListTracker("tok", http=http)
    await tracker.push(WatchProgress(AnimeId(anilist=None, mal=5), 1.0, 0, 0, datetime.now(timezone.utc)))
    assert http.calls == []  # nothing sent


async def test_pull_maps_media_list_to_progress():
    http = _FakeHttp([
        {"data": {"Viewer": {"id": 7, "name": "Ani"}}},
        {"data": {"MediaListCollection": {"lists": [{"entries": [
            {"progress": 5, "status": "CURRENT", "updatedAt": 1_700_000_000,
             "media": {"id": 196187, "title": {"romaji": "Show"}, "episodes": 12}},
            {"progress": 12, "status": "COMPLETED", "updatedAt": 0,
             "media": {"id": 21, "title": {"romaji": "One Piece"}, "episodes": None}},
        ]}]}}},
    ])
    tracker = AniListTracker("tok", http=http)
    rows = await tracker.pull_with_media()
    assert [(wp.anime_id.anilist, wp.episode, wp.completed) for wp, _ in rows] == [
        (196187, 5.0, False),
        (21, 12.0, True),
    ]
    assert rows[0][1].title.preferred == "Show"


# -- SyncService ------------------------------------------------------------- #
class _FakeTracker:
    name = "anilist"

    def __init__(self):
        self.pushed: list[tuple] = []
        self._pull = []

    async def push(self, progress, *, total=None):
        self.pushed.append((progress.anime_id.anilist, int(progress.episode), total))

    async def pull(self):
        return [p for p, _ in self._pull]

    async def pull_with_media(self):
        return self._pull


async def test_sync_push_sends_all_rows_with_totals():
    lib = FakeLibrary()
    await lib.save_anime(Anime(id=AnimeId(anilist=1), title=Title(romaji="A"), episode_count=12))
    await lib.save_progress(WatchProgress(AnimeId(anilist=1), 4.0, 0, 0, datetime.now(timezone.utc)))
    tracker = _FakeTracker()
    result = await SyncService(lib, tracker).push()
    assert result.pushed == 1
    assert tracker.pushed == [(1, 4, 12)]  # total resolved from cached metadata


async def test_sync_pull_imports_media_and_progress():
    lib = FakeLibrary()
    tracker = _FakeTracker()
    tracker._pull = [
        (WatchProgress(AnimeId(anilist=9), 3.0, 0, 0, datetime.now(timezone.utc)),
         Anime(id=AnimeId(anilist=9), title=Title(romaji="B"), episode_count=24)),
    ]
    result = await SyncService(lib, tracker).pull()
    assert result.pulled == 1
    assert lib.saved_anime[0].id.anilist == 9
    assert lib.saved[0].episode == 3.0


async def test_sync_disabled_without_tracker():
    svc = SyncService(FakeLibrary(), None)
    assert svc.enabled is False
    assert (await svc.push()).pushed == 0
