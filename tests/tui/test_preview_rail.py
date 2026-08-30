"""Region C leads with the row the cursor is on.

The rail was a second list — seven days of upcoming episodes and nothing else —
so the home screen was three lists beside a fourth, with no focal point and, in
a client for a visual medium, not one image on it.

It shows the highlighted show now: poster, what it is, how far in you are, and
what Enter will do. Two things about that are worth pinning down: the text must
never wait on the network, and moving the cursor must never become a request per
keypress.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anime_sh.domain.models import Anime, AnimeId, Format, Status, Title
from anime_sh.tui.preview import action, facts, lines

NOW = datetime.now(timezone.utc)


def _anime(**kw) -> Anime:
    base = dict(
        id=AnimeId(anilist=1),
        title=Title(romaji="Skeleton Knight in Another World Season 2"),
        format=Format.TV,
        status=Status.RELEASING,
        episode_count=12,
        year=2026,
    )
    base.update(kw)
    return Anime(**base)


def _plain(markup: str) -> str:
    import re

    return re.sub(r"\[/?[^\]]+\]", "", markup)


def test_the_panel_says_what_enter_will_do():
    """A percentage with no way to act on it leaves the reader to guess. The
    footer lists global keys; it cannot say what *this* row is offering."""
    resume = _plain(action(_anime(), 8, in_progress=True))
    fresh = _plain(action(_anime(), 3, in_progress=False))
    none = _plain(action(_anime(), None, in_progress=False))

    assert "resume" in resume and "8" in resume
    assert "play" in fresh and "resume" not in fresh
    assert "open" in none


def test_a_row_you_have_not_started_draws_no_progress_bar():
    """"Ready to watch" and "half watched" are different states, and a bar at
    zero reads as the second one."""
    body = "\n".join(lines(_anime(), 40, resume_episode=4, fraction=0.0))
    assert "%" not in body

    started = "\n".join(lines(_anime(), 40, resume_episode=4, fraction=0.42))
    assert "42%" in _plain(started)


def test_the_synopsis_cannot_style_the_panel():
    """AniList descriptions carry HTML, and square brackets in a title or a
    synopsis are markup to Textual — which would either restyle the rail or
    swallow everything after them."""
    body = "\n".join(lines(
        _anime(synopsis="A <i>very</i> [b]bold[/b] tale.<br>It continues."), 40
    ))
    assert "<i>" not in body and "<br>" not in body
    assert "[b]bold[/b]" not in body


def test_a_long_synopsis_is_cut_on_a_word():
    """Broken mid-word, an excerpt reads as damage."""
    text = " ".join(["antidisestablishmentarian"] * 40)
    body = lines(_anime(synopsis=text), 30, synopsis_lines=3)
    prose = [_plain(x) for x in body if "antidis" in x]
    assert prose, "the synopsis did not render at all"
    assert all(len(x) <= 30 for x in prose), "a line overflowed the rail"
    assert prose[-1].endswith("…"), "no sign that the synopsis was cut"


def test_facts_stay_on_one_line_and_drop_what_is_missing():
    """A row built from a cached record may know almost nothing about the show;
    the panel must not print 'None · None'."""
    full = _plain(facts(_anime(average_score=72)))
    assert "TV" in full and "12 eps" in full and "2026" in full and "72%" in full

    bare = _plain(facts(_anime(episode_count=None, year=None, average_score=None)))
    assert "None" not in bare and bare.strip() == "TV"


def test_an_airing_show_says_when_the_next_episode_lands():
    body = _plain("\n".join(lines(
        _anime(next_airing_episode=9, next_airing_at=NOW + timedelta(days=1, hours=4)),
        40,
    )))
    assert "Ep 9" in body and "1d" in body


# --------------------------------------------------------------------------- #
# The part that can hurt: fetching
# --------------------------------------------------------------------------- #
async def test_walking_the_cursor_does_not_fetch_a_cover_per_row():
    """The regression this screen has already had once, from a different cause.

    Holding an arrow key walks the cursor through a dozen rows a second. A
    request per highlighted row would be the launch storm again with a new
    trigger — and it was AniList rate-limiting that turned that storm into
    "Search failed: rate limited" on everything typed afterwards.

    The debounce window is held open for the whole test rather than raced
    against: written the obvious way — press keys, hope the timer has not fired
    — this failed one run in three, which is worse than not testing it at all.
    """
    import anime_sh.tui.screens.home as home_mod

    from .test_home_design import _app, _settle

    requested: list[str] = []

    async def _spy(url):
        requested.append(url)
        return None

    original = home_mod.fetch_cover
    home_mod.fetch_cover = _spy
    home_mod.HomeScreen._COVER_DEBOUNCE_S = 3600
    try:
        app = _app()
        async with app.run_test(size=(200, 44)) as pilot:
            await _settle(app, pilot)
            requested.clear()
            for _ in range(8):
                await pilot.press("down")
            await pilot.pause()
            assert not requested, (
                f"{len(requested)} covers requested while the cursor was moving"
            )
    finally:
        home_mod.fetch_cover = original
        home_mod.HomeScreen._COVER_DEBOUNCE_S = 0.35


async def test_the_cover_of_the_row_you_stop_on_is_fetched_once():
    """And exactly once: the window closes on the row that was rested on, not
    on every row walked past to reach it."""
    import anime_sh.tui.screens.home as home_mod

    from .test_home_design import _app, _settle

    requested: list[str] = []

    async def _spy(url):
        requested.append(url)
        return None

    original = home_mod.fetch_cover
    home_mod.fetch_cover = _spy
    home_mod.HomeScreen._COVER_DEBOUNCE_S = 0.01
    try:
        app = _app()
        async with app.run_test(size=(200, 44)) as pilot:
            await _settle(app, pilot)
            requested.clear()
            await pilot.press("down")
            await pilot.pause(0.2)
            await app.workers.wait_for_complete()
            assert len(requested) == 1, f"expected one request, got {len(requested)}"
    finally:
        home_mod.fetch_cover = original
        home_mod.HomeScreen._COVER_DEBOUNCE_S = 0.35


async def test_a_poster_is_fetched_once_however_often_you_pass_the_row():
    """Arrowing down and back up again is the most ordinary thing to do on this
    screen. Without a cache it re-downloads the same image every time.

    Writing this found a second hole: a fetch that *failed* was not recorded at
    all, so a show whose cover 404s was re-requested on every single pass. The
    attempt is cached now, not just the result.
    """
    import anime_sh.tui.screens.home as home_mod

    from .test_home_design import _app, _settle

    requested: list[str] = []

    async def _spy(url):
        requested.append(url)
        return b"not-a-real-image"  # painting is decoration; it may fail

    original = home_mod.fetch_cover
    home_mod.fetch_cover = _spy
    home_mod.HomeScreen._COVER_DEBOUNCE_S = 0.01
    try:
        app = _app()
        async with app.run_test(size=(200, 44)) as pilot:
            await _settle(app, pilot)
            requested.clear()
            for key in ("down", "up", "down", "up"):
                await pilot.press(key)
                await pilot.pause(0.15)
                await app.workers.wait_for_complete()
            assert len(set(requested)) == len(requested) <= 2, (
                f"the same posters were fetched repeatedly: {requested}"
            )
    finally:
        home_mod.fetch_cover = original
        home_mod.HomeScreen._COVER_DEBOUNCE_S = 0.35


async def test_a_cover_that_cannot_be_fetched_is_not_retried_forever():
    """A 404 or a timeout is an answer too. Recording only successes meant a
    show whose poster is missing sent a fresh request every time the cursor
    passed its row — quietly, since nothing appears either way."""
    import anime_sh.tui.screens.home as home_mod

    from .test_home_design import _app, _settle

    requested: list[str] = []

    async def _spy(url):
        requested.append(url)
        return None  # the fetch failed

    original = home_mod.fetch_cover
    home_mod.fetch_cover = _spy
    home_mod.HomeScreen._COVER_DEBOUNCE_S = 0.01
    try:
        app = _app()
        async with app.run_test(size=(200, 44)) as pilot:
            await _settle(app, pilot)
            requested.clear()
            for key in ("down", "up", "down", "up"):
                await pilot.press(key)
                await pilot.pause(0.15)
                await app.workers.wait_for_complete()
            assert len(requested) == len(set(requested)), (
                f"a failing cover was re-requested: {requested}"
            )
    finally:
        home_mod.fetch_cover = original
        home_mod.HomeScreen._COVER_DEBOUNCE_S = 0.35


def test_the_panel_places_the_show_before_it_summarises_it():
    """Genres tell you whether a show is for you faster than a paragraph of
    plot does, and the panel had room for both."""
    body = _plain("\n".join(lines(
        _anime(genres=("Action", "Fantasy", "Comedy", "Drama"), studio="Studio X"), 44
    )))
    assert "Action · Fantasy · Comedy" in body
    assert "Drama" not in body, "all four genres crowded out the synopsis"
    assert "Studio X" in body


def test_a_show_with_no_genres_does_not_get_an_empty_line():
    """Cached rows often know nothing but a title; a blank slot where the tags
    would be reads as something failing to load."""
    body = lines(_anime(genres=(), studio=None), 44)
    assert not any(part.strip() == "·" for part in body)
    assert body[1].strip(), "the facts line went missing"
