"""AniList tracker + SyncService — offline via a fake HTTP client / fake tracker."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

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
    # Obviously-fake credentials on purpose. This test carried a real AniList
    # client id for months, because it was the handiest value to type while
    # writing it. anime-sh ships no credentials of its own and every user
    # registers their own app, so none belong here either.
    http = _FakeHttp([{"access_token": "T.O.K", "token_type": "Bearer"}])
    tok = await exchange_code("00000", "sekret", "the-code", http=http)
    assert tok == "T.O.K"
    sent = http.calls[0]
    assert sent["grant_type"] == "authorization_code"
    assert sent["client_id"] == "00000" and sent["client_secret"] == "sekret"
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


def _statuses(*entries):
    """The two responses a push reads before its mutation: the viewer lookup and
    the status-only list query. ``entries`` are ``(media_id, status)`` pairs."""
    return [
        {"data": {"Viewer": {"id": 7, "name": "Ani"}}},
        {"data": {"MediaListCollection": {"lists": [{"entries": [
            {"status": s, "media": {"id": i}} for i, s in entries
        ]}]}}},
    ]


def _saved(http):
    """The variables of the SaveMediaListEntry mutation a push sent."""
    saves = [c for c in http.calls if "SaveMediaListEntry" in c["query"]]
    assert len(saves) == 1, f"expected one save, got {len(saves)}"
    return saves[0]["variables"]


async def _push(http, media_id, episode, total):
    tracker = AniListTracker("tok", http=http)
    await tracker.push(
        WatchProgress(AnimeId(anilist=media_id), float(episode), 0, 0,
                      datetime.now(timezone.utc), completed=True),
        total=total,
    )
    return tracker


async def test_push_sets_progress_and_completed_status():
    http = _FakeHttp(_statuses((196187, "CURRENT")) + [
        {"data": {"SaveMediaListEntry": {"id": 1, "progress": 12, "status": "COMPLETED"}}}
    ])
    await _push(http, 196187, 12, 12)
    assert _saved(http) == {"mediaId": 196187, "progress": 12, "status": "COMPLETED"}


async def test_push_uses_current_status_mid_season():
    http = _FakeHttp(_statuses((196187, "CURRENT")) + [{"data": {"SaveMediaListEntry": {"id": 1}}}])
    await _push(http, 196187, 3, 12)
    assert _saved(http)["status"] == "CURRENT"


# -- a push must not undo a status the user chose ---------------------------- #
# `_status_for` answered "COMPLETED if this is the finale, else CURRENT" without
# ever looking at what the entry already said. So pushing progress for a show
# you had dropped, paused or were rewatching silently moved it into Watching,
# and `sync push` did that across the whole library in one go.

@pytest.mark.parametrize("existing", ["DROPPED", "PAUSED", "REPEATING"])
async def test_push_keeps_a_status_the_user_deliberately_set(existing):
    http = _FakeHttp(_statuses((196187, existing)) + [{"data": {"SaveMediaListEntry": {"id": 1}}}])
    await _push(http, 196187, 3, 12)
    assert _saved(http)["status"] == existing, "a progress push rewrote the user's status"


async def test_finishing_a_show_still_completes_it():
    """The one status change a progress push is entitled to make."""
    http = _FakeHttp(_statuses((196187, "CURRENT")) + [{"data": {"SaveMediaListEntry": {"id": 1}}}])
    await _push(http, 196187, 12, 12)
    assert _saved(http)["status"] == "COMPLETED"


async def test_a_rewatch_is_not_ended_by_reaching_the_finale():
    """AniList bumps the repeat counter when a rewatch completes and this
    mutation does not touch that counter, so ending one here would lose it."""
    http = _FakeHttp(_statuses((196187, "REPEATING")) + [{"data": {"SaveMediaListEntry": {"id": 1}}}])
    await _push(http, 196187, 12, 12)
    assert _saved(http)["status"] == "REPEATING"


async def test_a_completed_show_is_not_reopened_when_the_total_is_unknown():
    """`total` comes from the locally cached `episode_count`. When that is
    missing the finale cannot be recognised, and the old rule fell through to
    CURRENT — pulling finished shows back into Watching on every push."""
    http = _FakeHttp(_statuses((196187, "COMPLETED")) + [{"data": {"SaveMediaListEntry": {"id": 1}}}])
    await _push(http, 196187, 12, None)
    assert _saved(http)["status"] == "COMPLETED"


async def test_a_show_not_yet_on_the_list_starts_as_watching():
    http = _FakeHttp(_statuses((999, "DROPPED")) + [{"data": {"SaveMediaListEntry": {"id": 1}}}])
    await _push(http, 196187, 3, 12)
    assert _saved(http)["status"] == "CURRENT"


async def test_the_status_lookup_happens_once_per_tracker():
    """Pushing the whole library must not re-download the list per show."""
    http = _FakeHttp(_statuses((1, "DROPPED"), (2, "CURRENT")) + [
        {"data": {"SaveMediaListEntry": {"id": 1}}},
        {"data": {"SaveMediaListEntry": {"id": 2}}},
    ])
    tracker = AniListTracker("tok", http=http)
    for media_id in (1, 2):
        await tracker.push(
            WatchProgress(AnimeId(anilist=media_id), 3.0, 0, 0, datetime.now(timezone.utc)),
            total=12,
        )
    lookups = [c for c in http.calls if "MediaListCollection" in c["query"]]
    assert len(lookups) == 1
    sent = [c["variables"]["status"] for c in http.calls if "SaveMediaListEntry" in c["query"]]
    assert sent == ["DROPPED", "CURRENT"]


async def test_a_failed_status_lookup_does_not_block_the_push():
    """Best-effort: the episode number is the point, the status is the refinement."""
    http = _FakeHttp([{"data": {"Viewer": None}}, {"data": {"SaveMediaListEntry": {"id": 1}}}])
    await _push(http, 196187, 12, 12)
    assert _saved(http) == {"mediaId": 196187, "progress": 12, "status": "COMPLETED"}


async def test_push_skips_when_no_anilist_id():
    http = _FakeHttp([])
    tracker = AniListTracker("tok", http=http)
    await tracker.push(WatchProgress(AnimeId(anilist=None, mal=5), 1.0, 0, 0, datetime.now(timezone.utc)))
    assert http.calls == []  # nothing sent


async def test_fetch_list_maps_status_and_score():
    http = _FakeHttp([
        {"data": {"Viewer": {"id": 7, "name": "Ani"}}},
        {"data": {"MediaListCollection": {"lists": [{"entries": [
            {"progress": 5, "status": "CURRENT", "updatedAt": 1_700_000_200, "score": 8.5,
             "media": {"id": 1, "title": {"romaji": "Now"}, "episodes": 12}},
            {"progress": 0, "status": "PLANNING", "updatedAt": 1_700_000_100, "score": 0,
             "media": {"id": 2, "title": {"romaji": "Later"}, "episodes": 24}},
        ]}]}}},
    ])
    tracker = AniListTracker("tok", http=http)
    entries = await tracker.fetch_list()
    # Newest-updated first; status + score preserved.
    assert [(e.anime.id.anilist, e.status, e.progress, e.score) for e in entries] == [
        (1, "CURRENT", 5, 8.5),
        (2, "PLANNING", 0, 0.0),
    ]


async def test_set_status_and_score_send_expected_mutations():
    http = _FakeHttp([
        {"data": {"SaveMediaListEntry": {"id": 1, "status": "COMPLETED"}}},
        {"data": {"SaveMediaListEntry": {"id": 1, "score": 9.0}}},
    ])
    tracker = AniListTracker("tok", http=http)
    await tracker.set_status(196187, "completed")
    assert http.calls[0]["variables"] == {"id": 196187, "status": "COMPLETED"}
    await tracker.set_score(196187, 9.0)
    assert http.calls[1]["variables"] == {"id": 196187, "scoreRaw": 90}  # 0-100


async def test_set_status_rejects_unknown_and_score_out_of_range():
    import pytest
    from anime_sh.domain.errors import MetadataError

    tracker = AniListTracker("tok", http=_FakeHttp([]))
    with pytest.raises(MetadataError):
        await tracker.set_status(1, "bogus")
    with pytest.raises(MetadataError):
        await tracker.set_score(1, 42)


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
    # AniList progress = N means N episodes are finished, so the N-th is
    # completed regardless of the list status (the detail screen then treats
    # every earlier episode as watched too). A progress-0 planning entry stays
    # not-completed.
    assert [(wp.anime_id.anilist, wp.episode, wp.completed) for wp, _ in rows] == [
        (196187, 5.0, True),
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


async def test_push_keeps_going_when_one_row_is_rejected():
    """One bad row must not cost you the rest of the push.

    A single rejected media id (deleted entry, rate limit that outlasted its
    retries) aborted the whole run, losing every row still queued behind it.
    """
    from anime_sh.app.sync import SyncService
    from anime_sh.domain.models import AnimeId, WatchProgress

    def _prog(anilist_id: int) -> WatchProgress:
        return WatchProgress(
            anime_id=AnimeId(anilist=anilist_id), episode=1.0, position_s=0,
            duration_s=0, updated_at=datetime.now(timezone.utc), completed=True,
        )

    class Library:
        async def all_progress_rows(self):
            return [_prog(1), _prog(2), _prog(3)]

        async def get_anime(self, anime_id):
            return None

    class Tracker:
        name = "anilist"

        def __init__(self):
            self.seen = []

        async def push(self, progress, *, total=None):
            self.seen.append(progress.anime_id.anilist)
            if progress.anime_id.anilist == 2:
                raise RuntimeError("media does not exist")

    tracker = Tracker()
    result = await SyncService(Library(), tracker).push()
    assert tracker.seen == [1, 2, 3]  # row 3 was still attempted
    assert (result.pushed, result.skipped) == (2, 1)


async def test_sync_push_sends_one_call_per_show_not_per_episode():
    """A tracker entry holds one number. Sending every row for a show set that
    entry over and over, ending on the highest — the same result as sending only
    the highest, for as many calls as the show has episodes. On a real library
    that was 152 calls for 76 shows.
    """
    lib = FakeLibrary()
    await lib.save_anime(Anime(id=AnimeId(anilist=1), title=Title(romaji="A"),
                               episode_count=28))
    now = datetime.now(timezone.utc)
    for ep in (1.0, 2.0, 3.0, 28.0):
        await lib.save_progress(WatchProgress(AnimeId(anilist=1), ep, 0, 0, now))
    tracker = _FakeTracker()
    result = await SyncService(lib, tracker).push()

    assert tracker.pushed == [(1, 28, 28)], "one call, carrying the furthest episode"
    assert result.pushed == 1, "the CLI reports this as 'show(s)'"


async def test_sync_push_never_sets_a_show_below_where_you_are():
    """The reason it matters. Every intermediate call set the entry *below* your
    real progress, so a push interrupted partway — a dropped connection, a rate
    limit outlasting its retries — left finished shows parked at a low episode.
    """
    lib = FakeLibrary()
    await lib.save_anime(Anime(id=AnimeId(anilist=1), title=Title(romaji="A"),
                               episode_count=28))
    now = datetime.now(timezone.utc)
    for ep in (1.0, 2.0, 3.0, 28.0):
        await lib.save_progress(WatchProgress(AnimeId(anilist=1), ep, 0, 0, now))
    tracker = _FakeTracker()
    await SyncService(lib, tracker).push()

    # Not "ends on the highest" — never *sends* anything lower at all, so there
    # is no moment during the push at which the account reads low.
    assert [ep for _, ep, _ in tracker.pushed] == [28]


async def test_sync_push_picks_the_furthest_even_if_rows_arrive_unordered():
    """The repository happens to ORDER BY episode today. The property being
    relied on is "furthest", which should not depend on that staying true."""
    now = datetime.now(timezone.utc)

    class Library:
        async def all_progress_rows(self):
            return [WatchProgress(AnimeId(anilist=1), ep, 0, 0, now)
                    for ep in (12.0, 3.0, 7.0)]

        async def get_anime(self, anime_id):
            return None

    tracker = _FakeTracker()
    await SyncService(Library(), tracker).push()
    assert tracker.pushed == [(1, 12, None)]
