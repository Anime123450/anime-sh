"""How the home screen divides its height between the shelves.

Two properties, and the redesign broke both at different times:

* the column fills. A first version handed each shelf a strictly proportional
  share and deliberately kept the remainder — a shelf clamped to its own length
  gives nothing back, so at 200x60 the four shelves took 36 of 59 rows and
  twenty-three sat empty beneath a label that still said "8 of 19".
* the column is not four equal blocks. The fix for the above was tried first as
  an *even* redistribution, which flattened the weighting it was meant to
  preserve: This Season drew level with Continue Watching and the screen read as
  a dashboard again.

Neither shows up in a render test, because both versions render cleanly. They
are properties of the arithmetic, so they are tested on the arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from anime_sh.domain.models import AnimeId, FavoriteItem, WatchProgress
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.shell import SECTIONS

from .test_app import FakePlayback, FakeSearch, ResumeItem, _noop
from .test_home_design import _anime

#: Long enough that every shelf wants more rows than any terminal will give it,
#: which is the case the division has to get right. A fixture of four rows per
#: list makes every allocation look correct because nothing is ever scarce.
_N = 20


class _Library:
    async def continue_watching(self, *, limit=20):
        return [
            ResumeItem(
                anime=_anime(i, f"Show Number {i}"),
                progress=WatchProgress(AnimeId(anilist=i), 3.0, 300, 1400,
                                       datetime.now(timezone.utc)),
            )
            for i in range(1, _N + 1)
        ]

    async def progress_for(self, anime_id):
        return []

    async def favorites(self):
        now = datetime.now(timezone.utc)
        return [FavoriteItem(anime=_anime(900 + i, name), added_at=now)
                for i, name in enumerate(("A Favourite", "Another Favourite"))]


class _Metadata:
    name = "fake"

    async def trending(self, *, limit=20):
        return [_anime(100 + i, f"Trending {i}") for i in range(_N)]

    async def seasonal(self, season, year):
        return [_anime(200 + i, f"Seasonal {i}") for i in range(_N)]

    async def sequel(self, anime_id):
        return None


def _app() -> AnimeShApp:
    return AnimeShApp(
        TuiServices(search=FakeSearch(), metadata=_Metadata(), library=_Library(),
                    playback=FakePlayback(), aclose=_noop),
        theme="tokyo-night",
    )


async def _settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    for _ in range(4):
        await pilot.pause()


async def test_the_shelves_fill_the_height_they_are_given():
    """No block of dead rows under the last shelf.

    Checked at three heights, because the bug was invisible at the small one:
    at 24 rows the proportional pass happened to spend everything, and only a
    tall terminal revealed that the remainder was never handed back.
    """
    for size in ((120, 24), (120, 40), (200, 60)):
        app = _app()
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            screen = app.screen
            caps = screen._compute_caps()
            chrome = len(caps) * screen._SHELF_CHROME + 2  # the two bars
            used = sum(caps.values()) + chrome
            # One row of slack for rounding. Twenty-three was the bug.
            assert used >= size[1] - 1, (
                f"{size}: {used} of {size[1]} rows used, "
                f"{size[1] - used} left empty — caps {caps}"
            )
            assert used <= size[1] + len(caps), (
                f"{size}: {used} rows allocated into {size[1]} — the column "
                f"will scroll when it should fit; caps {caps}"
            )


async def test_continue_watching_stays_the_biggest_shelf():
    """Hierarchy has to be visible in size, not only in order.

    `Section.weight` is what says so, and an even redistribution of the
    leftover silently cancelled it — every shelf came out the same height and
    the only thing distinguishing the one you came for was that it was first.
    """
    weights = {s.list_id: s.weight for s in SECTIONS}
    lead = next(s for s in SECTIONS if s.weight == max(weights.values()))

    for size in ((120, 40), (160, 50)):
        app = _app()
        async with app.run_test(size=size) as pilot:
            await _settle(app, pilot)
            caps = app.screen._compute_caps()
            others = [v for k, v in caps.items() if k != lead.list_id]
            assert caps[lead.list_id] > max(others), (
                f"{size}: {lead.label} got {caps[lead.list_id]} rows against "
                f"{max(others)} — the screen reads as equal blocks; caps {caps}"
            )
