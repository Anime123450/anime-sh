"""`anime resume` has to play the episode you have not watched.

Continue Watching keeps a show around after you *finish* its latest episode —
that is deliberate, and it is what makes the list useful while a season is still
airing. So the top row's `episode` is often one you have already seen, and
`resume` played it unconditionally: on real data it would have replayed Solo
Leveling S2 episode 6, announcing "Episode 6 at 0s", when episode 7 was the one
waiting.

The TUI never had this bug — `continue_cells` works out the episode to act on for
each row. The CLI reimplemented the easy half and the two disagreed about the same
database, which is why the predicate now lives in the domain next to the data it
reads.
"""

from __future__ import annotations

from datetime import datetime, timezone

from anime_sh.domain.models import AnimeId, WatchProgress

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
ID = AnimeId(anilist=176496)


def _progress(episode, position_s, duration_s, completed):
    return WatchProgress(
        anime_id=ID, episode=episode, position_s=position_s,
        duration_s=duration_s, completed=completed, updated_at=NOW,
    )


def test_a_finished_episode_sends_you_to_the_next_one():
    """The case this was found on: ep 6 watched to the end, ep 7 waiting."""
    p = _progress(6.0, 0, 0, True)
    assert not p.resumable
    assert p.next_episode == 7.0, "resume must not replay what you finished"


def test_a_part_watched_episode_is_the_one_to_resume():
    p = _progress(8.0, 262, 1429, False)
    assert p.resumable
    assert p.next_episode == 8.0
    assert round(p.fraction * 100) == 18


def test_an_episode_marked_elsewhere_sends_you_to_the_next_one():
    """`anime mark` and the AniList pull both record progress with no position or
    duration, because no player was involved. Those are finished episodes, not
    episodes someone is nought percent into."""
    p = _progress(21.0, 0, 0, True)
    assert not p.resumable
    assert p.next_episode == 22.0


def test_a_position_without_a_duration_still_resumes_that_episode():
    """mpv can be closed before it ever reports a duration, leaving a real
    position with nothing to measure it against. The percentage is unknowable;
    *which episode you were in* is not. Requiring a duration here sent this row
    to the next episode and threw away the five minutes it had -- a worse bug
    than the one that rule was added to fix."""
    p = _progress(4.0, 300, 0, False)
    assert p.resumable
    assert p.next_episode == 4.0, "a known position must not be discarded"
    assert p.fraction == 0.0, "and there is still no percentage to draw"


def test_the_very_start_of_an_episode_is_not_resumable():
    """position 0 with a known duration: opened and closed without watching
    anything. There is nothing to resume *to*, so the next episode is the useful
    answer and matches what the TUI already offers."""
    p = _progress(4.0, 0, 1420, False)
    assert not p.resumable
    assert p.next_episode == 5.0


def test_half_episode_numbers_survive():
    """Recaps and specials are numbered 6.5, and the arithmetic must not quietly
    turn one into 7 while you are part-way through it."""
    assert _progress(6.5, 400, 1400, False).next_episode == 6.5
    assert _progress(6.5, 0, 0, True).next_episode == 7.5


def test_the_tui_and_the_domain_agree_on_which_rows_are_resumable():
    """The TUI had this right first. If the two ever disagree, Continue Watching
    draws a progress bar for an episode `resume` will skip, or the other way
    round -- and the screen and the command would be describing different shows.
    """
    import pytest

    pytest.importorskip("textual")
    from anime_sh.tui.format import continue_cells
    from anime_sh.domain.models import Anime, Status, Title

    anime = Anime(
        id=ID, title=Title(romaji="Solo Leveling Season 2"),
        status=Status.FINISHED, episode_count=13,
    )
    for progress in (
        _progress(6.0, 0, 0, True),
        _progress(8.0, 262, 1429, False),
        _progress(4.0, 0, 1420, False),
        _progress(4.0, 300, 0, False),  # position, no duration: the hard case
    ):
        built = continue_cells(anime, progress)
        assert built is not None
        _, resume_episode = built
        assert resume_episode == progress.next_episode, (
            f"the TUI would act on {resume_episode} and resume on "
            f"{progress.next_episode} for the same row"
        )
