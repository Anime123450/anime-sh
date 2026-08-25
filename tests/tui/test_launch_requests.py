"""What the home screen asks the network for when it opens.

Launching used to fire one AniList query per Continue-Watching row — twenty at
once — on top of seasonal, trending and the AniList sync. AniList rate-limits
well below that, so a normal launch earned a 429; and because the limiter is
shared, the next thing typed failed too, with two identical toasts stacked over
the list:

    Search failed: AniList is rate-limiting requests: rate limited —
    try again in about 41s

Almost none of those requests could return anything new. These tests pin down
which ones are worth making.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Format,
    ResumeItem,
    Status,
    Title,
    WatchProgress,
)
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.screens.home import _schedule_is_stale

from .test_app import FakeLibrary, FakeMetadata, FakePlayback, FakeSearch, _noop

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _show(anilist: int, **kw) -> Anime:
    base = dict(id=AnimeId(anilist=anilist), title=Title(romaji=f"Show {anilist}"),
                format=Format.TV, episode_count=12)
    base.update(kw)
    return Anime(**base)


# --- which rows are worth a request ---------------------------------------- #

def test_a_finished_show_is_never_refetched():
    """Its schedule is final. This is most of a library, and most of the storm."""
    assert not _schedule_is_stale(_show(1, status=Status.FINISHED), NOW)
    assert not _schedule_is_stale(_show(2, status=Status.CANCELLED), NOW)


def test_an_airing_show_with_a_future_episode_is_not_refetched():
    """The countdown ticks locally from the cached timestamp, so the row already
    has everything it needs and the request would buy nothing."""
    airing = _show(3, status=Status.RELEASING, next_airing_episode=6,
                   next_airing_at=NOW + timedelta(days=2))
    assert not _schedule_is_stale(airing, NOW)


def test_an_airing_show_whose_episode_has_aired_is_refetched():
    aired = _show(4, status=Status.RELEASING, next_airing_episode=6,
                  next_airing_at=NOW - timedelta(hours=1))
    assert _schedule_is_stale(aired, NOW)


def test_an_unknown_status_is_refetched_rather_than_assumed_finished():
    """`Status` defaults to UNKNOWN, and a row saved bare — on playback, say —
    carries that default.

    Reading "unknown" as "finished, nothing can change" would pin the row to a
    schedule it never had and offer an unreleased episode as though it were
    waiting for you. That is the bug the cached schedule exists to prevent, so
    the cheap answer here is the wrong one.
    """
    assert _schedule_is_stale(_show(5, status=Status.UNKNOWN), NOW)
    assert _schedule_is_stale(_show(6, status=Status.NOT_YET_RELEASED), NOW)
    assert _schedule_is_stale(_show(7, status=Status.HIATUS), NOW)


# --- what that adds up to on launch ---------------------------------------- #

class CountingMetadata(FakeMetadata):
    """Counts `get` calls and records how many were in flight at once."""

    def __init__(self, answer: Anime | None = None):
        self.calls: list[int] = []
        self.peak = 0
        self._live = 0
        self._answer = answer

    async def get(self, anime_id):
        self._live += 1
        self.peak = max(self.peak, self._live)
        self.calls.append(anime_id.anilist)
        try:
            # Must actually suspend. Without an await here every call runs to
            # completion before the next starts, `peak` never exceeds 1, and the
            # concurrency assertion below passes against unbounded fan-out —
            # verified by reverting the gate and watching it pass anyway.
            await asyncio.sleep(0.01)
            return self._answer or _show(anime_id.anilist, status=Status.FINISHED)
        finally:
            self._live -= 1


def _app(library, metadata) -> AnimeShApp:
    """Deliberately not a context manager: `run_test` installs Textual context
    vars, and entering it inside an async generator leaves them owned by the
    generator's context rather than the test's ("Token was created in a
    different Context"). Each test enters it directly."""
    services = TuiServices(search=FakeSearch(), metadata=metadata,
                           library=library, playback=FakePlayback(), aclose=_noop)
    return AnimeShApp(services, theme="tokyo-night")


async def _settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_a_library_of_finished_shows_makes_no_metadata_requests():
    """Twenty finished shows: twenty requests before, none now."""
    library = FakeLibrary()
    library.continue_items = [
        ResumeItem(
            anime=_show(i, status=Status.FINISHED),
            progress=WatchProgress(AnimeId(anilist=i), 3.0, 0, 1400, NOW,
                                   completed=True),
        )
        for i in range(1, 21)
    ]
    metadata = CountingMetadata()
    app = _app(library, metadata)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
    assert metadata.calls == [], f"expected no refetches, got {len(metadata.calls)}"


async def test_the_requests_that_are_needed_go_out_a_few_at_a_time():
    """The rows that genuinely need refreshing still must not all leave at once —
    AniList's budget is shared with seasonal, trending and sync, which are in
    flight at the same moment."""
    library = FakeLibrary()
    library.continue_items = [
        ResumeItem(
            anime=_show(i, status=Status.RELEASING, next_airing_episode=4,
                        next_airing_at=NOW - timedelta(days=400)),
            progress=WatchProgress(AnimeId(anilist=i), 3.0, 0, 1400, NOW,
                                   completed=True),
        )
        for i in range(1, 21)
    ]
    metadata = CountingMetadata()
    app = _app(library, metadata)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
    assert len(metadata.calls) == 20  # all genuinely stale
    assert metadata.peak <= 4, f"{metadata.peak} requests in flight at once"


async def test_a_repeated_message_does_not_stack_a_second_toast():
    """A rate limit hit by one loader and again by the next is one fact. Two
    identical toasts covered the rows behind an error already read."""
    library = FakeLibrary()
    metadata = CountingMetadata()
    app = _app(library, metadata)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        # Textual's notify() returns None either way, so assert the real
        # effect: how many notifications the app is holding. It posts a message
        # rather than appending directly, so each one needs a pump cycle before
        # it can be counted.
        app._recent_toast = ("", 0.0)
        before = len(app._notifications)

        app.notify("AniList rate limited — try again in about 41s")
        await pilot.pause()
        assert len(app._notifications) == before + 1

        app.notify("AniList rate limited — try again in about 41s")
        await pilot.pause()
        assert len(app._notifications) == before + 1, "the repeat stacked a toast"

        app.notify("something else entirely")
        await pilot.pause()
        assert len(app._notifications) == before + 2
