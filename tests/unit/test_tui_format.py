"""Pure TUI presentation helpers: countdown, score badge, meta line, cover art."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anime_sh.domain.models import Anime, AnimeId, Format, Status, Title
from anime_sh.tui.coverart import render_cover
from anime_sh.domain.models import WatchProgress
from anime_sh.tui.format import (
    continue_row,
    countdown,
    episode_air_label,
    home_subtitle,
    meta_line,
    next_episode_line,
    score_badge,
    waiting_subtitle,
)

_NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def _anime(**kw):
    base = dict(id=AnimeId(anilist=1), title=Title(romaji="Show"))
    base.update(kw)
    return Anime(**base)


def test_countdown_scales_by_magnitude():
    assert countdown(_NOW + timedelta(days=5, hours=3), _NOW) == "in 5d 3h"
    assert countdown(_NOW + timedelta(hours=2, minutes=10), _NOW) == "in 2h 10m"
    assert countdown(_NOW + timedelta(minutes=40), _NOW) == "in 40m"
    assert countdown(_NOW - timedelta(hours=1), _NOW) == "airing now"


def test_score_badge_color_thresholds():
    assert "green" in score_badge(91)
    assert "yellow" in score_badge(65)
    assert "red" in score_badge(40)
    assert score_badge(None) is None
    assert score_badge(0) is None


def test_meta_line_includes_studio_and_score():
    a = _anime(format=Format.TV, status=Status.RELEASING, episode_count=12,
               year=2026, studio="MADHOUSE", average_score=82)
    line = meta_line(a)
    assert "TV" in line and "Releasing" in line and "12 eps" in line
    assert "2026" in line and "MADHOUSE" in line and "82%" in line


def test_next_episode_line_only_when_airing_data_present():
    airing = _anime(next_airing_episode=3, next_airing_at=_NOW + timedelta(days=1))
    assert next_episode_line(airing, _NOW) == "Ep 3 in 1d 0h"
    assert next_episode_line(_anime(), _NOW) is None


def test_home_subtitle_airing_shows_aired_over_total_and_countdown():
    a = _anime(format=Format.TV, status=Status.RELEASING, episode_count=12,
               next_airing_episode=3, next_airing_at=_NOW + timedelta(days=4, hours=6))
    # 2 aired of 12 (next is ep 3), plus the countdown — not the planned "12 eps".
    assert home_subtitle(a, _NOW) == "TV · 2/12 eps · Ep 3 in 4d 6h"


def test_home_subtitle_airing_without_total():
    a = _anime(format=Format.TV, status=Status.RELEASING,
               next_airing_episode=4, next_airing_at=_NOW + timedelta(hours=5))
    assert home_subtitle(a, _NOW) == "TV · 3 eps · Ep 4 in 5h 0m"


def test_home_subtitle_finished_shows_total_and_year():
    a = _anime(format=Format.TV, status=Status.FINISHED, episode_count=12, year=2026)
    assert home_subtitle(a, _NOW) == "TV · 12 eps · 2026"


def test_home_subtitle_airing_without_schedule_falls_back():
    # RELEASING but AniList has no nextAiringEpisode (between cours) → no bogus count.
    a = _anime(format=Format.TV, status=Status.RELEASING, episode_count=24, year=2026)
    assert home_subtitle(a, _NOW) == "TV · 24 eps · 2026"


def test_waiting_subtitle_caught_up_shows_countdown():
    # Airing show, next is ep 6 (5 aired), you've watched ep 5 → caught up.
    a = _anime(status=Status.RELEASING, next_airing_episode=6,
               next_airing_at=_NOW + timedelta(days=2, hours=3))
    assert waiting_subtitle(a, 5.0, _NOW) == "caught up · Ep 6 in 2d 3h"


def test_waiting_subtitle_none_when_episodes_left_to_watch():
    # Same show, but you've only watched ep 3 of the 5 aired → still actionable.
    a = _anime(status=Status.RELEASING, next_airing_episode=6,
               next_airing_at=_NOW + timedelta(days=2))
    assert waiting_subtitle(a, 3.0, _NOW) is None


def test_waiting_subtitle_none_for_finished_show():
    a = _anime(status=Status.FINISHED, episode_count=12)
    assert waiting_subtitle(a, 12.0, _NOW) is None


def _prog(episode, pos, dur=1400, completed=False):
    return WatchProgress(anime_id=AnimeId(anilist=1), episode=episode,
                         position_s=pos, duration_s=dur,
                         updated_at=_NOW, completed=completed)


def test_continue_row_resume_in_progress_episode():
    a = _anime(status=Status.RELEASING, episode_count=12)
    assert continue_row(a, _prog(4.0, 700), _NOW) == ("Ep 4 · 50%", False, 4.0)


def test_continue_row_up_next_when_more_available():
    # Finished ep 3 of a 12-ep finished show → next one's already out.
    a = _anime(status=Status.FINISHED, episode_count=12)
    assert continue_row(a, _prog(3.0, 1400, completed=True), _NOW) == (
        "up next · Ep 4", False, 4.0)


def test_continue_row_caught_up_airing_is_dimmed_with_countdown():
    a = _anime(status=Status.RELEASING, next_airing_episode=6,
               next_airing_at=_NOW + timedelta(days=2, hours=3))
    sub, dim, resume = continue_row(a, _prog(5.0, 1400, completed=True), _NOW)
    assert sub == "caught up · Ep 6 in 2d 3h" and dim is True and resume == 6.0


def test_continue_row_dropped_when_finished_and_fully_watched():
    a = _anime(status=Status.FINISHED, episode_count=12)
    assert continue_row(a, _prog(12.0, 1400, completed=True), _NOW) is None


def test_episode_air_label_projects_weekly_from_next_airing():
    a = _anime(status=Status.RELEASING, next_airing_episode=5,
               next_airing_at=_NOW + timedelta(days=2))
    assert episode_air_label(a, 5, _NOW) == "airs in 2d 0h"      # the next one
    assert episode_air_label(a, 7, _NOW) == "airs in 16d 0h"     # +2 weeks
    assert episode_air_label(a, 4, _NOW) is None                 # already aired
    assert episode_air_label(_anime(), 3, _NOW) is None          # no schedule


# -- cover art (Pillow-backed, graceful) ------------------------------------- #
def _png(w, h, color=(200, 40, 40)) -> bytes:
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_render_cover_produces_block_grid():
    from anime_sh.tui.coverart import _QUADRANTS

    art = render_cover(_png(60, 90), cols=10)
    assert art is not None
    rows = art.plain.split("\n")
    assert all(len(r) == 10 for r in rows)          # every row is `cols` wide
    assert all(ch in _QUADRANTS for ch in art.plain if ch != "\n")
    assert art.spans, "expected per-cell color styles"


def test_render_cover_preserves_color():
    # A solid poster renders that colour (a smooth block picks a single-colour
    # split, not a muddied two-colour one).
    art = render_cover(_png(30, 45, color=(200, 40, 40)), cols=8)
    assert art is not None
    assert "200,40,40" in str(art.spans[0].style)


def test_render_cover_snaps_edges_to_two_colors():
    # A red-over-blue split: a cell on the boundary is coloured with two
    # distinct colours (proof the split follows the edge, not a fixed threshold).
    from PIL import Image
    import io

    img = Image.new("RGB", (20, 40))
    for y in range(40):
        for x in range(20):
            img.putpixel((x, y), (220, 20, 20) if y < 20 else (20, 20, 220))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    art = render_cover(buf.getvalue(), cols=8)
    styles = [str(s.style) for s in art.spans]
    assert any(" on " in st and st.split(" on ")[0] != st.split(" on ")[1]
               for st in styles)


def test_render_cover_returns_none_on_garbage():
    assert render_cover(b"not an image", cols=10) is None
    assert render_cover(b"", cols=10) is None
