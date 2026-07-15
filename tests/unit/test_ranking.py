"""Pure ranking logic — no I/O, deterministic."""

from __future__ import annotations

from anime_sh.domain.models import Quality, Stream, StreamKind
from anime_sh.domain.ranking import pick_stream, quality_score


def _s(q: Quality) -> Stream:
    return Stream(url=f"u-{q.value}", kind=StreamKind.HLS, quality=q)


def test_quality_ordering():
    assert quality_score(Quality.Q2160) > quality_score(Quality.Q1080)
    assert quality_score(Quality.Q360) > quality_score(Quality.UNKNOWN)


def test_pick_best():
    streams = [_s(Quality.Q480), _s(Quality.Q1080), _s(Quality.Q720)]
    assert pick_stream(streams, "best").quality == Quality.Q1080


def test_pick_worst():
    streams = [_s(Quality.Q480), _s(Quality.Q1080), _s(Quality.Q720)]
    assert pick_stream(streams, "worst").quality == Quality.Q480


def test_pick_specific_prefers_at_or_below():
    streams = [_s(Quality.Q480), _s(Quality.Q1080), _s(Quality.Q720)]
    assert pick_stream(streams, "720p").quality == Quality.Q720


def test_pick_specific_falls_back_above_when_none_below():
    streams = [_s(Quality.Q1080), _s(Quality.Q2160)]
    assert pick_stream(streams, "480p").quality == Quality.Q1080


def test_pick_empty_returns_none():
    assert pick_stream([], "best") is None
