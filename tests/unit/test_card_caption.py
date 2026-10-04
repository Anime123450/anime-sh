"""The line under a poster says the one thing the poster cannot.

Fourteen cells, so each branch is checked for what it says *and* for fitting —
a caption that overflows is silently cut by the card, and the half that
survives ("ep 6 in 2") reads as data rather than as damage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anime_sh.domain.models import Anime, AnimeId, Status, Title, WatchProgress
from anime_sh.tui.cards import CARD_COLS
from anime_sh.tui.format import card_caption

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _anime(**kw) -> Anime:
    base = dict(id=AnimeId(anilist=1), title=Title(romaji="Show"))
    return Anime(**{**base, **kw})


def _progress(episode: float, *, position_s: int = 0,
              duration_s: int = 1400, completed: bool = False) -> WatchProgress:
    return WatchProgress(anime_id=AnimeId(anilist=1), episode=episode,
                         position_s=position_s, duration_s=duration_s,
                         updated_at=NOW, completed=completed)


def _fits(caption: str) -> bool:
    return len(caption) <= CARD_COLS


def test_a_part_watched_episode_shows_how_far_in():
    cap = card_caption(_anime(episode_count=12),
                       _progress(5, position_s=600))
    assert cap == "ep 5 · 43%"
    assert _fits(cap)


def test_a_known_position_without_a_duration_still_resumes():
    """mpv can quit before it reports a duration; the episode is still known."""
    cap = card_caption(_anime(episode_count=12),
                       _progress(5, position_s=600, duration_s=0))
    assert cap == "resume ep 5"
    assert _fits(cap)


def test_aired_but_unwatched_episodes_are_counted():
    a = _anime(episode_count=12, status=Status.RELEASING,
               next_airing_episode=9, next_airing_at=NOW + timedelta(days=2))
    one = card_caption(a, _progress(7, completed=True), now=NOW)
    assert one == "ep 8 ready", "one episode waiting should name it"
    many = card_caption(a, _progress(5, completed=True), now=NOW)
    assert many == "3 eps ready"
    assert _fits(one) and _fits(many)


def test_caught_up_on_an_airing_show_counts_down_instead():
    a = _anime(episode_count=12, status=Status.RELEASING,
               next_airing_episode=9, next_airing_at=NOW + timedelta(days=2))
    cap = card_caption(a, _progress(8, completed=True), now=NOW)
    assert cap.startswith("ep 9 ")
    assert _fits(cap)


def test_an_untracked_show_says_how_much_of_it_exists():
    assert card_caption(_anime(episode_count=12)) == "12 eps"
    assert card_caption(_anime(episode_count=1)) == "1 ep"


def test_a_show_with_nothing_to_say_says_nothing():
    """Not a placeholder. A card whose caption is unknown shows a blank line,
    which is what reserving the row looks like; inventing "?" or "TBA" there
    puts a fact on screen that nobody has."""
    assert card_caption(_anime()) == ""
    assert card_caption(_anime(year=2024)) == "2024"


def test_the_shelf_leads_with_what_you_can_act_on():
    """Reading order is the whole argument for Continue Watching: the episode you
    are part-way through is the likeliest thing you opened the app to do, so it
    leads, and the shows you cannot act on at all sink to the bottom. Ordering by
    recency instead put a half-watched episode below three shows that merely had
    a new episode out.

    Built through `card_caption` on purpose — the sort reads the caption rather
    than recomputing the state, so a reworded caption that no longer says
    "ready" would silently drop every waiting episode to the bottom of the
    shelf, and this is what notices.
    """
    import pytest

    pytest.importorskip("textual")
    from anime_sh.tui.screens.home import _readiness

    airing = _anime(episode_count=12, status=Status.RELEASING,
                    next_airing_episode=9, next_airing_at=NOW + timedelta(days=2))
    cases = {
        "resume": (_anime(episode_count=12), _progress(5, position_s=600)),
        "ready": (airing, _progress(5, completed=True)),
        "waiting": (airing, _progress(8, completed=True)),
    }
    # The same four-tuple the shelf builds: (anime, caption, episode, fraction).
    built = {
        name: (a, card_caption(a, prog, NOW), prog.next_episode,
               prog.fraction if prog.resumable else 0.0)
        for name, (a, prog) in cases.items()
    }
    order = sorted(built, key=lambda name: _readiness(built[name]))
    assert order == ["resume", "ready", "waiting"]
