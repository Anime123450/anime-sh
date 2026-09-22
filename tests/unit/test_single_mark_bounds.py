"""A single `mark` could name an episode that does not exist, and the tracker
would believe it.

`mark --single` skipped every ceiling check, so `mark "Frieren" -e 99999
--single` was legal on a 28-episode show. That is not just an odd local row:
`sync push` sends the *furthest* episode per show, so the next push would set
AniList to 99999 for that title — the same shape of damage this project has
already done once, reachable now through a single typo.

The ceiling is deliberately generous. A single mark legitimately can exceed a
known total: AniList's count lags an airing season, and specials are sometimes
numbered past the finale.
"""

from __future__ import annotations

import pytest

from anime_sh.app.library import LibraryService
from anime_sh.domain.errors import AnimeShError
from anime_sh.domain.models import Anime, AnimeId, Title

FINISHED = Anime(
    id=AnimeId(anilist=154587), title=Title(romaji="Frieren"), episode_count=28
)
UNKNOWN = Anime(id=AnimeId(anilist=21), title=Title(romaji="One Piece"))


@pytest.fixture
def service():
    # _check_mark_bounds is pure validation and touches no repository.
    return LibraryService(library=None)


def _check(service, anime, episode, *, single=True):
    service._check_mark_bounds(anime, episode, single=single)


# -- the bug ---------------------------------------------------------------- #
def test_a_wild_single_mark_is_refused(service):
    with pytest.raises(AnimeShError) as ei:
        _check(service, FINISHED, 99999)
    message = str(ei.value)
    assert "28 episodes" in message
    # The message has to say why it matters, or it reads as pedantry.
    assert "furthest" in message and "AniList" in message


def test_a_wild_single_mark_is_refused_when_the_count_is_unknown(service):
    with pytest.raises(AnimeShError, match="2000"):
        _check(service, UNKNOWN, 99999)


# -- what must still work --------------------------------------------------- #
def test_episode_zero_is_still_allowed(service):
    """Some shows number a prologue 0; that is the reason singles relax the
    floor at all."""
    _check(service, FINISHED, 0)


def test_the_finale_is_allowed(service):
    _check(service, FINISHED, 28)


def test_a_little_past_a_stale_count_is_allowed(service):
    """AniList's episode_count lags an airing season, and specials get numbered
    past the finale. Refusing these would break real marking."""
    _check(service, FINISHED, 30)
    _check(service, FINISHED, 28 + LibraryService.SINGLE_MARK_HEADROOM)


def test_far_past_the_count_is_refused(service):
    with pytest.raises(AnimeShError):
        _check(service, FINISHED, 28 + LibraryService.SINGLE_MARK_HEADROOM + 1)


def test_a_long_running_show_can_still_be_marked_deep(service):
    """One Piece is past 1100 episodes with no count on some entries."""
    _check(service, UNKNOWN, 1200)


# -- catch-up behaviour is untouched ---------------------------------------- #
def test_catch_up_still_refuses_past_the_finale(service):
    with pytest.raises(AnimeShError, match="28 episodes"):
        _check(service, FINISHED, 29, single=False)


def test_catch_up_still_refuses_zero(service):
    """A catch-up to 0 names an empty range, which used to silently mark
    nothing while reporting success."""
    with pytest.raises(AnimeShError, match="1 or higher"):
        _check(service, FINISHED, 0, single=False)


def test_catch_up_to_the_finale_is_allowed(service):
    _check(service, FINISHED, 28, single=False)
