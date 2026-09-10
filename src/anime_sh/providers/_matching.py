"""Title matching shared by the HiAnime-family providers.

Both anikoto and hianime search a site by title and have to decide which of a
dozen near-identical results is the show AniList meant. The heuristics below —
particularly the airing-status inversion in :func:`rank_items` — were tuned
against real mismatches, and a second copy of them would drift.

Items are plain dicts because each provider parses a different markup into
them; the only keys read here are ``title``, ``jp`` and ``sub_eps``.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from ..domain.models import Anime, Status


def norm(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())

def search_terms(anime: Anime) -> list[str]:
    terms = [anime.title.romaji, anime.title.english, *anime.title.synonyms]
    seen, out = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out or [anime.title.preferred]


# Fuzzy gate: keep near-matches, not just exact titles, so alternate entries
# (a "[Mini]" batch, a slightly different romanisation) still surface.
MATCH_THRESHOLD = 0.55


def scored_matches(anime: Anime, items: list[dict]) -> list[dict]:
    """Items whose title fuzzily matches the show, each tagged with ``_score``
    (title similarity). Not ranked — see :func:`rank_items`."""
    targets = [
        norm(t)
        for t in (anime.title.romaji, anime.title.english, *anime.title.synonyms)
        if t
    ]
    out: list[dict] = []
    for item in items:
        names = [item.get("title"), item.get("jp")]
        sim = max(
            (
                SequenceMatcher(None, norm(n), tgt).ratio()
                for n in names if n for tgt in targets
            ),
            default=0.0,
        )
        if sim >= MATCH_THRESHOLD:
            item = dict(item)
            item["_score"] = round(sim, 3)
            out.append(item)
    return out


def rank_items(anime: Anime, items: list[dict]) -> list[dict]:
    """Order matches best-first: among genuinely-similar titles, prefer the one
    whose available episode count is closest to AniList's planned total (or the
    most complete when unknown) — so a full "[Mini]" batch outranks a same-named
    TV run that Anikoto has stalled on.

    While the show is still AIRING that heuristic inverts: no entry can have the
    planned total yet, so one that does (e.g. a finished "[Mini]" spin-off with
    the same name) is a *different* production. There the closest title wins,
    tie-broken by the most-stocked entry within the planned total."""
    if not items:
        return []
    best_sim = max(it["_score"] for it in items)
    releasing = anime.status is Status.RELEASING

    def rank(item: dict) -> tuple:
        strong = item["_score"] >= best_sim - 0.15
        eps = item.get("sub_eps")
        want = anime.episode_count
        if releasing:
            within = eps if strong and eps and (want is None or eps <= want) else 0
            return (-item["_score"], -within)
        if strong and want and eps:
            ep_key = abs(eps - want)
        elif strong and eps:
            ep_key = -eps
        else:
            ep_key = 10_000
        return (ep_key, -item["_score"])

    return sorted(items, key=rank)


def to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
