"""Auto-next on a show with no announced episode total.

AniList leaves ``episodes`` null for a currently-airing show whose total run has
never been announced — One Piece is the obvious one, and it is also exactly the
kind of show someone sits down and binges. ``nextAiringEpisode`` is still
populated for those, so what has aired *is* known; it is only the planned total
that isn't.
"""

from datetime import datetime, timezone

from anime_sh.app.playback import PlaybackService
from anime_sh.domain.models import Anime, AnimeId, Status, Title

has_next = PlaybackService._has_next


def _show(*, planned: int | None, next_ep: int | None,
          status: Status = Status.RELEASING) -> Anime:
    return Anime(
        id=AnimeId(anilist=1),
        title=Title(romaji="Long Runner"),
        status=status,
        episode_count=planned,
        next_airing_episode=next_ep,
        next_airing_at=datetime(2026, 10, 5, tzinfo=timezone.utc) if next_ep else None,
    )


def test_a_long_runner_with_no_announced_total_still_advances():
    """The bug: auto-next stopped after a single episode on these shows.

    `episode_count is None` was checked before the airing schedule, so a show
    with 1149 episodes out and no announced total answered "no next episode"
    from episode 1 onwards.
    """
    one_piece = _show(planned=None, next_ep=1150)
    assert has_next(None, one_piece, 1.0) is True
    assert has_next(None, one_piece, 1148.0) is True


def test_it_still_stops_at_the_last_aired_episode():
    one_piece = _show(planned=None, next_ep=1150)
    assert has_next(None, one_piece, 1149.0) is False
    assert has_next(None, one_piece, 1200.0) is False


def test_knowing_nothing_at_all_still_stops():
    """No total and no schedule means there is nothing to advance *into* — the
    one case where refusing is the only safe answer."""
    assert has_next(None, _show(planned=None, next_ep=None), 4.0) is False


def test_the_planned_total_still_rules_a_finished_show():
    done = _show(planned=12, next_ep=None, status=Status.FINISHED)
    assert has_next(None, done, 11.0) is True
    assert has_next(None, done, 12.0) is False
