"""Pure TUI presentation helpers: countdown, score badge, meta line, cover art."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anime_sh.domain.models import Anime, AnimeId, Format, Status, Title
from anime_sh.tui.coverart import render_cover
from anime_sh.tui.format import (
    countdown,
    home_subtitle,
    meta_line,
    next_episode_line,
    score_badge,
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


# -- cover art (Pillow-backed, graceful) ------------------------------------- #
def _png(w, h, color=(200, 40, 40)) -> bytes:
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_render_cover_produces_half_block_grid():
    art = render_cover(_png(60, 90), cols=10)
    assert art is not None
    rows = art.plain.split("\n")
    assert all(len(r) == 10 for r in rows)  # every row is `cols` wide
    assert rows and all(ch == "▀" for ch in rows[0])
    assert art.spans, "expected per-cell color styles"


def test_render_cover_returns_none_on_garbage():
    assert render_cover(b"not an image", cols=10) is None
    assert render_cover(b"", cols=10) is None
