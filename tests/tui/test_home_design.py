"""The home screen's visual structure, asserted against a mounted screen.

These are the properties of the *rendered* composition — the ones no unit test
can see, and which stayed green through the whole of the previous redesign
being wrong. A hero over horizontal shelves of cover art lives or dies on three
of them:

* the shelves sit on a plate above the background, so they read as surfaces;
* a card reserves its full size before its cover lands, so nothing reflows;
* Tab walks shelves and nothing else, because the containers accept focus too.
"""

from __future__ import annotations

from datetime import datetime, timezone

from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Format,
    Title,
    WatchProgress,
)
from anime_sh.tui import AnimeShApp, TuiServices
from anime_sh.tui.cards import CARD_COLS, CARD_ROWS, PosterCard, Shelf

from .test_app import FakePlayback, FakeSearch, ResumeItem, _noop


def _anime(anilist: int, title: str, eps: int = 12) -> Anime:
    # A cover URL on every show: the shelves fetch one per card, and a fixture
    # without one would make the reserved-space tests pass by having nothing to
    # reserve space for.
    return Anime(id=AnimeId(anilist=anilist), title=Title(romaji=title),
                 format=Format.TV, episode_count=eps, year=2023,
                 cover_url=f"https://example.invalid/{anilist}.jpg")


# Deliberately lopsided: Continue Watching holds the long titles and This Season
# the short ones. Under a card a title is cut to fourteen cells either way, which
# is exactly the case where a layout that sizes itself to its content drifts.
_LONG = [
    "Rich Girl Caretaker: I'm Secretly the Caregiver of the Most Popular Girl",
    "The Duke's Son Claims He Won't Love Me Yet Showers Me with Adoration",
    "That Time I Got Reincarnated as a Slime Season 4",
    "Skeleton Knight in Another World Season 2",
]
_SHORT = ["BLACK TORCH", "Goodbye, Lara", "Sparks of Tomorrow", "Victoria"]


class _Library:
    async def continue_watching(self, *, limit=20):
        out = []
        for i, name in enumerate(_LONG, start=1):
            prog = WatchProgress(AnimeId(anilist=i), 3.0, 300, 1400,
                                 datetime.now(timezone.utc))
            out.append(ResumeItem(anime=_anime(i, name), progress=prog))
        return out

    async def progress_for(self, anime_id):
        return []

    async def favorites(self):
        return []


class _Metadata:
    name = "fake"

    async def trending(self, *, limit=20):
        return [_anime(90 + i, n) for i, n in enumerate(_SHORT)]

    async def seasonal(self, season, year):
        return [_anime(50 + i, n) for i, n in enumerate(_SHORT)]

    async def sequel(self, anime_id):
        return None


def _app() -> AnimeShApp:
    services = TuiServices(search=FakeSearch(), metadata=_Metadata(),
                           library=_Library(), playback=FakePlayback(),
                           aclose=_noop)
    return AnimeShApp(services, theme="tokyo-night")


async def _settle(app, pilot) -> None:
    """Let the screen finish. Covers arrive from a worker and the hero repaints
    one refresh behind focus, so a single pause reads a screen still moving."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    for _ in range(4):
        await pilot.pause()


async def test_the_shelves_sit_on_a_plate_above_the_background():
    """Depth comes from background tiers, not from boxes — a border costs two
    terminal rows per shelf to say what a shade already says.

    The stylesheet once set `Screen` to `$surface`, the *middle* tier, which
    left nowhere to go up: shelves and background were one colour and the screen
    read as a flat wall. Asserted as "these differ", not as a hex value, so a
    theme change cannot break it.
    """
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        base = app.screen.styles.background
        plate = app.screen.query_one("#continue", Shelf).styles.background

        assert plate != base, "the shelves sit on the same colour as the background"
        assert plate.a, "a transparent plate is no plate at all"


async def test_every_card_is_the_same_size():
    """A shelf is a grid, and a grid with one odd cell in it is not one.

    A card's size is fixed rather than derived from its content for exactly this
    reason: the titles differ in length by a factor of five, and a card that
    sized itself would make each shelf a ragged row of differently-shaped
    rectangles.
    """
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        sizes = {
            (c.content_region.width, c.content_region.height)
            for c in app.screen.query(PosterCard)
            if c.display and c.content_region.width
        }
        assert sizes, "test premise: no card was laid out"
        assert len(sizes) == 1, f"cards came out at different sizes: {sizes}"
        width, height = sizes.pop()
        assert width == CARD_COLS, f"a card is {width} cells, not {CARD_COLS}"
        assert height == CARD_ROWS, (
            f"a card has {height} rows to draw {CARD_ROWS} in — the bottom one "
            f"is its state caption, and it is being clipped"
        )


async def test_a_card_reserves_its_full_height_before_its_cover_arrives():
    """Nothing may move when a poster lands.

    Covers arrive one HTTPS round trip at a time, so a card that grew to fit its
    art would shunt every shelf below it down the screen a dozen times on
    launch. The placeholder is the exact size the art will be — which is the
    whole reason it exists rather than the card simply being left blank.
    """
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        card = next(c for c in app.screen.query(PosterCard) if c.display)
        before = card.content_region.height

        card.set_cover(None)  # a cover that failed: must change nothing
        await pilot.pause()
        assert card.content_region.height == before

        # And a real one. Rendering is Pillow's job and may be unavailable; what
        # is asserted is the height, which must hold either way.
        card.set_cover(_PNG)
        await pilot.pause()
        assert card.content_region.height == before, (
            "the card changed height when its cover landed"
        )


#: The smallest valid PNG: 1x1, black. Enough for `render_cover` to accept.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


async def test_the_selected_card_is_the_only_one_marked():
    """With eleven posters visible there is no "the middle one is selected"
    convention to lean on, so selection is drawn — and drawn once."""
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        shelf = app.screen.query_one("#continue", Shelf)
        marked = [c for c in shelf.cards if c.selected]
        assert len(marked) == 1, f"{len(marked)} cards claim to be selected"
        assert marked[0] is shelf.cards[shelf.index]


async def test_the_hero_describes_the_card_under_the_cursor():
    """The whole argument for a hero: detail for one show, the one you point at.

    Without this the screen is a wall of posters with a permanently stale panel
    over it, which is worse than no panel.
    """
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        shelf = app.screen.query_one("#continue", Shelf)
        assert len(shelf.cards) > 1, "test premise: one card cannot show movement"

        await pilot.press("right")
        await pilot.pause()
        wanted = shelf.cards[shelf.index].anime
        hero = str(app.screen.query_one("#hero-text").render())
        # The first word of the title, because the hero wraps and cuts it.
        head = wanted.title.preferred.split()[0]
        assert head in hero, (
            f"the hero does not mention {head!r}; it is still on another show"
        )


async def test_tab_moves_between_shelves_and_nowhere_else():
    """Both the README and the `?` sheet say Tab means "next shelf", and
    Textual's default focus chain does not do that.

    Left alone it walks every focusable widget, which on this screen includes
    `#body` — a scroll container that accepts focus, draws no cursor, and turns
    the arrow keys into panel scrolling.
    """
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)

        seen = []
        for _ in range(6):
            await pilot.press("tab")
            await pilot.pause()
            seen.append(app.focused)

        assert all(isinstance(w, Shelf) for w in seen), (
            "Tab landed on something that is not a shelf: "
            + ", ".join(f"#{w.id}" if w is not None else "nothing" for w in seen)
        )
        assert "body" not in [w.id for w in seen], "Tab reached the scroll container"


async def test_tab_wraps_round_rather_than_stopping():
    """A cycle that dead-ends at the last shelf makes the key feel broken."""
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        first = app.focused.id
        seen = set()
        for _ in range(8):
            await pilot.press("tab")
            await pilot.pause()
            seen.add(app.focused.id)
        assert first in seen, "Tab never came back round to where it started"


async def test_shift_tab_goes_the_other_way():
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        start = app.focused.id
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused.id == start


async def test_down_and_up_move_between_shelves():
    """`HorizontalScroll` inherits bindings for both and would swallow them to
    scroll vertically — in a container exactly one card tall, so the key did
    nothing at all and never reached the screen."""
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        start = app.focused.id
        await pilot.press("down")
        await pilot.pause()
        assert app.focused.id != start, "down did not leave the shelf"
        await pilot.press("up")
        await pilot.pause()
        assert app.focused.id == start, "up did not come back"


async def test_tab_skips_a_shelf_with_nothing_in_it():
    """Favourites stays empty until you star something, and searching hides the
    browse shelves. Landing on either is landing nowhere."""
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        empty = [s.id for s in app.screen.query(Shelf)
                 if not s.cards or not s.display]
        assert empty, "test premise: no empty or hidden shelf to skip"

        for _ in range(6):
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused.id not in empty, (
                f"Tab stopped on #{app.focused.id}, which has no cards"
            )


async def test_a_toast_does_not_land_where_a_shelf_is_heading():
    """Textual docks notifications bottom-*right*, which is the end of a shelf
    you are scrolling towards — a shelf runs left to right, so the right-hand
    edge is where the next posters appear from. A toast is 60 cells wide and sits
    there for five seconds.

    Asserted on the computed style rather than by screenshotting a toast:
    notifications are not mounted at all in headless mode, so a rendered check
    here would pass whether or not the rule applied.
    """
    from textual.widgets._toast import ToastRack

    from .test_app import _make_app

    app, _ = _make_app()
    async with app.run_test(size=(190, 50)) as pilot:
        await pilot.pause()
        rack = ToastRack()
        await app.screen.mount(rack)
        await pilot.pause()
        assert rack.styles.align_horizontal == "left", (
            "toasts are back on the right, over the end of a shelf"
        )
        assert rack.styles.align_vertical == "bottom"


async def test_a_shelf_that_could_not_load_says_so_instead_of_going_blank():
    """The day AniList disabled its own public API, This Season and Trending
    went empty: a heading with no count above blank space. The failure *was*
    announced — once, in a toast, which is gone by the time anyone looks — and
    an empty "Trending" reads as "nothing is trending", not as "we could not
    reach AniList".
    """
    from textual.widgets import Label

    from .test_app import _make_app

    class _Down:
        name = "down"
        async def trending(self, *, limit=30):
            raise RuntimeError("The AniList API has been temporarily disabled")
        async def seasonal(self, season, year):
            raise RuntimeError("The AniList API has been temporarily disabled")
        async def get(self, *a, **k):
            return None

    app, _ = _make_app()
    app.services.metadata = _Down()
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        for sec, lst in (("#sec-trending", "#trending"),
                         ("#sec-seasonal", "#seasonal")):
            heading = str(app.screen.query_one(sec, Label).content)
            assert "unavailable" in heading, f"{sec} is silently blank: {heading!r}"
            assert app.screen.query_one(lst).display is False, (
                f"{lst} left an empty plate on screen"
            )
