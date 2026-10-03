"""The mouse works on everything that looks like it should answer one.

Textual's `Footer` is clickable — `FooterKey.on_mouse_down` calls
`simulate_key` — so replacing it with a prettier `Static` quietly removed every
mouse affordance the app had, and a row of keys that invites a click and then
ignores it is worse than no row at all.

None of this shows up in a render test: the bar paints identically whether or
not anything is listening.

The poster shelves have their own version of the problem. A wall of cover art
is the most obviously clickable thing the app has ever drawn, and `Shelf` is a
`HorizontalScroll` — a container, with no notion of a selection to move.
"""

from __future__ import annotations

import asyncio

from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.cards import PosterCard, Shelf

from datetime import datetime, timezone

from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Format,
    Title,
    WatchProgress,
)

from .test_app import FakePlayback, FakeSearch, ResumeItem, _noop


def _anime(anilist: int, title: str) -> Anime:
    return Anime(id=AnimeId(anilist=anilist), title=Title(romaji=title),
                 format=Format.TV, episode_count=12, year=2026,
                 cover_url=f"https://example.invalid/{anilist}.jpg")


#: Shared between Continue Watching and This Season, which is what gives
#: `test_a_hidden_card_is_not_clickable` something hidden to not click. The
#: default fixture in `test_app` has one row a shelf and no overlap, so the
#: premise of half this file was vacuously true against it — and a test whose
#: premise is false passes while checking nothing.
_SHARED = 7


class _Library:
    async def continue_watching(self, *, limit=20):
        return [
            ResumeItem(
                anime=_anime(i, f"Continue Show {i}"),
                progress=WatchProgress(AnimeId(anilist=i), 3.0, 300, 1400,
                                       datetime.now(timezone.utc)),
            )
            for i in (1, 2, 3, _SHARED)
        ]

    async def progress_for(self, anime_id):
        return []

    async def favorites(self):
        return []


class _Metadata:
    name = "fake"

    async def trending(self, *, limit=20):
        return [_anime(90 + i, f"Trending {i}") for i in range(4)]

    async def seasonal(self, season, year):
        # The shared show first, so hiding it also moves the cursor.
        return [_anime(_SHARED, f"Continue Show {_SHARED}"),
                *(_anime(50 + i, f"Seasonal {i}") for i in range(3))]

    async def sequel(self, anime_id):
        return None


def _app() -> AnimeShApp:
    return AnimeShApp(
        TuiServices(search=FakeSearch(), metadata=_Metadata(),
                    library=_Library(), playback=FakePlayback(), aclose=_noop),
        theme="tokyo-night",
    )


async def _settle(app, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    for _ in range(4):
        await pilot.pause()


async def test_clicking_a_card_selects_it_and_clicking_again_opens_it():
    """Two steps, not one.

    A poster wall is something you point at to *look* at — the hero above
    updates as the cursor moves — and a single stray click launching a video
    player is a worse mistake than one extra click.
    """
    app = _app()
    async with app.run_test(size=(160, 50)) as pilot:
        await _settle(app, pilot)
        shelf = app.screen.query_one("#continue", Shelf)
        cards = shelf.cards
        assert len(cards) > 1, "test premise: more than one card to click between"
        assert shelf.index == 0

        await pilot.click(cards[1])
        await pilot.pause()
        assert shelf.index == 1, "clicking a card did not move the cursor to it"
        assert type(app.screen).__name__ == "HomeScreen", (
            "the first click on a card opened it; it should only select"
        )

        await pilot.click(cards[1])
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert type(app.screen).__name__ != "HomeScreen", (
            "clicking the selected card did not open it"
        )


async def test_clicking_a_card_moves_the_hero_to_it():
    """The hero is the reason the two-step click is worth it; it has to follow."""
    app = _app()
    async with app.run_test(size=(160, 50)) as pilot:
        await _settle(app, pilot)
        shelf = app.screen.query_one("#continue", Shelf)
        cards = shelf.cards
        assert len(cards) > 1

        await pilot.click(cards[1])
        await pilot.pause()
        assert app.screen._hero_id == cards[1].anime.id.anilist, (
            "the hero still describes the card the cursor left"
        )


async def test_clicking_the_action_bar_runs_the_key():
    """The regression Textual's own Footer would not have had."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        bar = app.screen.query_one("#actionbar")
        text = str(bar.render())
        assert "? help" in text, f"test premise: no help key on the bar: {text!r}"

        await pilot.click(bar, offset=(text.index("? help"), 0))
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.pause()

        assert type(app.screen).__name__ == "HelpScreen", (
            "clicking '? help' on the action bar did nothing"
        )


async def test_a_hidden_card_is_not_clickable():
    """This Season hides the shows already in Continue Watching.

    `display = False` takes the card out of the layout, so a click cannot land
    on it — but the shelf's cursor is an *index into its children*, which still
    counts the hidden ones. A cursor that can stop on a card nobody can see is
    a cursor that vanishes.
    """
    app = _app()
    async with app.run_test(size=(160, 50)) as pilot:
        await _settle(app, pilot)
        shelf = app.screen.query_one("#seasonal", Shelf)
        # `all_cards`, not `cards`: the filtering is the behaviour under test,
        # so asking the filtered list whether anything is hidden can only ever
        # answer no.
        hidden = [c for c in shelf.all_cards if not c.display]
        if not hidden:
            # The fixture overlaps Continue Watching with This Season; if that
            # ever stops being true this test is checking nothing, and should
            # say so rather than pass.
            raise AssertionError(
                "test premise: no seasonal card is hidden as a duplicate"
            )
        assert all(c.display for c in shelf.cards), (
            "a hidden card is still reachable by the cursor"
        )
        shelf.focus()
        await pilot.pause()
        assert shelf.cards[shelf.index].display, (
            "the cursor is parked on a card that is not displayed"
        )
        # And walking the shelf never finds one either.
        for _ in range(len(shelf.all_cards) + 1):
            await pilot.press("right")
            await pilot.pause()
            assert shelf.cards[shelf.index].display


async def test_every_shelf_label_is_a_label_and_not_a_control():
    """It says what the shelf is. Nothing more, so nothing invites a click.

    The previous design put a clickable `z` on any shelf it had truncated. The
    shelves hold everything they are given now, so there is nothing to expand —
    and a leftover affordance that does nothing is the exact failure this file
    exists for.
    """
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        await _settle(app, pilot)
        for head in ("#sec-continue", "#sec-favorites", "#sec-seasonal",
                     "#sec-trending"):
            text = str(app.screen.query_one(head).render())
            assert "@click" not in text, f"{head} still draws an affordance"


async def test_posters_are_mounted_for_every_shelf():
    """A sanity floor for the tests above: the cards exist and carry a show."""
    app = _app()
    async with app.run_test(size=(160, 50)) as pilot:
        await _settle(app, pilot)
        cards = list(app.screen.query(PosterCard))
        assert cards, "no poster cards were mounted at all"
        assert all(c.anime is not None for c in cards)
