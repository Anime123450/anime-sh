"""Deterministic, I/O-free ranking of streams and providers.

Kept pure so it is trivially unit-testable and can never be the reason a
search is slow. Everything here is a function of its arguments.
"""

from __future__ import annotations

from .models import Audio, Quality, Stream

_QUALITY_RANK: dict[Quality, int] = {
    Quality.Q2160: 5,
    Quality.Q1080: 4,
    Quality.Q720: 3,
    Quality.Q480: 2,
    Quality.Q360: 1,
    Quality.UNKNOWN: 0,
}

_QUALITY_TARGETS: dict[str, Quality] = {
    "best": Quality.Q2160,
    "1080p": Quality.Q1080,
    "720p": Quality.Q720,
    "480p": Quality.Q480,
    "worst": Quality.Q360,
}


def quality_score(q: Quality) -> int:
    return _QUALITY_RANK.get(q, 0)


def pick_stream(streams: list[Stream], target: str = "best") -> Stream | None:
    """Choose the stream closest to the desired quality.

    ``best`` / ``worst`` pick the extreme available; a specific quality picks
    the closest match, preferring not to exceed the requested ceiling.
    """
    if not streams:
        return None
    if target == "best":
        return max(streams, key=lambda s: quality_score(s.quality))
    if target == "worst":
        return min(streams, key=lambda s: quality_score(s.quality))

    want = _QUALITY_TARGETS.get(target, Quality.Q1080)
    want_rank = quality_score(want)
    # Prefer the highest quality that does not exceed the target; if none is
    # at-or-below, fall back to the lowest available above it.
    at_or_below = [s for s in streams if quality_score(s.quality) <= want_rank]
    if at_or_below:
        return max(at_or_below, key=lambda s: quality_score(s.quality))
    return min(streams, key=lambda s: quality_score(s.quality))


def provider_score(
    *,
    priority: int,
    health: float,
    best_quality: Quality,
    audio_match: bool,
) -> float:
    """Rank a provider's offering for a given episode. Higher is better."""
    return (
        priority * 10.0
        + health * 5.0
        + quality_score(best_quality)
        + (2.0 if audio_match else 0.0)
    )


def audio_matches(candidate_audio: Audio, wanted: Audio) -> bool:
    return candidate_audio == wanted
