"""The hero block, and the covers that fill it and the shelves below it.

The hero is what the whole composition is for: one show, large, described in
full — poster, what it is, how far in you are, and what Enter will do — for
whichever poster the cursor is on. `preview.lines` draws it, and the first half
of this file is that function on its own, because it is a pure function over an
already-loaded `Anime` and so is the cheapest place to pin the content.

The second half is the cover pipeline, where there are two invariants worth
holding onto. An image is fetched at most once per show, however many shelves it
appears on and however often the cursor passes it — and a fetch that *failed* is
an answer too, recorded like any other, or a show whose poster 404s sends a
fresh request forever, quietly, since nothing appears either way.
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
def _spying(monkeypatch) -> list[str]:
    """Record every cover fetch and answer nothing, without touching the disk.

    Both halves matter. `fetch_cover` is patched where the screen imported it,
    and `cached_cover` is patched too — otherwise a real cover already sitting in
    the user's cache directory answers the call and the fetch never happens, so
    the test passes by never exercising the thing it is about.
    """
    import anime_sh.tui.screens.home as home_mod

    seen: list[str] = []

    async def _fetch(url):
        seen.append(url)
        return None

    monkeypatch.setattr(home_mod, "fetch_cover", _fetch)
    monkeypatch.setattr(home_mod, "cached_cover", lambda url: None)
    return seen


async def test_walking_the_cursor_fetches_nothing(monkeypatch):
    """Moving along a shelf must not be a network request per keypress.

    The previous screen fetched the highlighted row's poster and so needed a
    debounce timer to survive a held-down arrow key — a launch storm with a
    different trigger, and the same mistake that once earned this screen a 429
    on every start. A shelf of posters has no such problem to solve: every card
    it holds was asked for when the shelf was built, so by the time the cursor
    can move there is nothing left to ask for.
    """
    from .test_home_design import _app, _settle

    seen = _spying(monkeypatch)
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        seen.clear()
        for key in ("right", "right", "left", "down", "right", "up"):
            await pilot.press(key)
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert not seen, (
            f"{len(seen)} covers requested while the cursor was moving: {seen}"
        )


async def test_a_cover_is_fetched_once_however_many_shelves_show_it(monkeypatch):
    """Trending and This Season overlap most weeks.

    A request per card would fetch the same image twice over, and then paint it
    into one of the two cards — leaving the same poster blank beside itself.
    """
    from .test_home_design import _app, _settle

    seen = _spying(monkeypatch)
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        assert seen, "test premise: no covers were requested at all"
        assert len(seen) == len(set(seen)), (
            "the same cover was fetched more than once: "
            + ", ".join(sorted(u for u in seen if seen.count(u) > 1))
        )


async def test_a_cover_that_cannot_be_fetched_is_not_retried_forever(monkeypatch):
    """A 404 or a timeout is an answer, and has to be recorded as one.

    The spy above answers None to every request, so a second pass over the same
    shows is exactly the failing case: re-running the loaders must not send the
    same requests again.
    """
    from .test_home_design import _app, _settle

    seen = _spying(monkeypatch)
    app = _app()
    async with app.run_test(size=(200, 50)) as pilot:
        await _settle(app, pilot)
        first = list(seen)
        assert first, "test premise: no covers were requested at all"

        seen.clear()
        # What coming back from a detail screen does: rebuild the shelves that
        # read from the library, with every cover already known to be missing.
        app.screen._load_continue()
        app.screen._load_favorites()
        await _settle(app, pilot)
        assert not seen, (
            f"covers that already failed were requested again: {seen}"
        )


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
