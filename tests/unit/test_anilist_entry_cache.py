"""A failed list read must not disable the backwards guard for the session.

`_existing_entries` is what tells a push "you are already at episode 12, do not
send 3". It is fetched once and cached, and its own comment says ``None`` means
not fetched yet, "which is not the same as 'the list is empty'" — but a failed
fetch cached ``{}``, which is exactly that. One rate-limited query and every
later push in the process pushed blind.

That matters because the tracker lives as long as the process and during a binge
that is many pushes: a single hiccup on the first episode turned a rewatch into
"roll my AniList back to episode 3" for the whole session.
"""

from __future__ import annotations

from datetime import datetime, timezone

from anime_sh.domain.models import AnimeId, WatchProgress
from anime_sh.infra.http import HttpError
from anime_sh.infra.tracker.anilist import AniListTracker


class _FlakyHttp:
    """Replies from a script; an exception in the script is raised instead."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def post_json(self, url, *, json=None, headers=None):
        self.calls.append(json)
        reply = self._responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def aclose(self):
        pass


_VIEWER = {"data": {"Viewer": {"id": 7, "name": "Ani"}}}
_SAVED = {"data": {"SaveMediaListEntry": {"id": 1}}}


def _statuses(media_id, status, progress):
    return {"data": {"MediaListCollection": {"lists": [{"entries": [
        {"status": status, "progress": progress, "media": {"id": media_id}}
    ]}]}}}


def _progress(media_id, episode):
    return WatchProgress(
        AnimeId(anilist=media_id), float(episode), 0, 0,
        datetime.now(timezone.utc), completed=True,
    )


def _saves(http):
    return [c for c in http.calls if "SaveMediaListEntry" in c["query"]]


async def test_a_failed_list_read_is_retried_on_the_next_push():
    """The bug: the second push never asked again, so it rolled AniList back.

    Episode 1 is pushed while the list query is down — unavoidable, nothing is
    known. By episode 2 the network is back, and the list says COMPLETED at 12,
    so that push must be refused.
    """
    http = _FlakyHttp([
        _VIEWER,
        HttpError("429 Too Many Requests"),
        _SAVED,                                  # ep 1 goes out blind
        _statuses(196187, "COMPLETED", 12),      # the retry, now answered
        _SAVED,                                  # must not be used
    ])
    tracker = AniListTracker("tok", http=http)

    await tracker.push(_progress(196187, 1), total=12)
    assert len(_saves(http)) == 1, "the blind first push should still go out"

    await tracker.push(_progress(196187, 2), total=12)
    assert len(_saves(http)) == 1, (
        "the second push rolled AniList back to episode 2 — the list read was "
        "never retried after the first one failed"
    )


async def test_a_successful_read_is_still_only_fetched_once():
    """The cache has to keep working, including for a genuinely empty list."""
    http = _FlakyHttp([
        _VIEWER,
        {"data": {"MediaListCollection": {"lists": []}}},
        _SAVED,
        _SAVED,
    ])
    tracker = AniListTracker("tok", http=http)
    await tracker.push(_progress(196187, 1), total=12)
    await tracker.push(_progress(196187, 2), total=12)

    lists = [c for c in http.calls if "MediaListCollection" in c["query"]]
    assert len(lists) == 1, f"queried the list {len(lists)} times, not once"
    assert len(_saves(http)) == 2
