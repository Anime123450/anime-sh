"""SearchService — forgiving orchestration over the metadata source.

Search touches only the metadata source (AniList), never a scraper: it is
instant and reliable, and provider availability is resolved lazily later, at
play time. That is what keeps search-as-you-type fast in the TUI.

AniList's own search is strict: it wants the title spelled the way it stores it.
``dont toy with me`` finds nothing (it wants ``don't``); ``atack on titan`` finds
nothing (one typo). So this service makes the *common* mistakes forgiving without
changing the fast path:

* A working query is passed straight through, in AniList's own relevance order —
  no reordering, no extra requests.
* Only when AniList returns **nothing** do we escalate: retry with apostrophes
  restored (``dont`` -> ``don't``) and with the query's distinctive words, then
  fuzzy-rank the merged pool against what the user actually typed.
"""

from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher

from ..domain.models import Anime, AnimeId, SearchResult
from ..domain.ports import MetadataSource

# No-apostrophe spellings people actually type, mapped to how a title stores it.
# We keep the raw query too, so a word that is *also* a real word (its, were)
# never loses its original meaning — this only adds a candidate.
_CONTRACTIONS = {
    "dont": "don't", "wont": "won't", "cant": "can't", "isnt": "isn't",
    "arent": "aren't", "wasnt": "wasn't", "werent": "weren't", "doesnt": "doesn't",
    "didnt": "didn't", "hasnt": "hasn't", "havent": "haven't", "couldnt": "couldn't",
    "wouldnt": "wouldn't", "shouldnt": "shouldn't", "aint": "ain't",
    "im": "i'm", "ive": "i've", "ill": "i'll", "id": "i'd",
    "youre": "you're", "youve": "you've", "youll": "you'll", "youd": "you'd",
    "hes": "he's", "shes": "she's", "its": "it's", "thats": "that's",
    "whats": "what's", "wheres": "where's", "theres": "there's", "heres": "here's",
    "hows": "how's", "whos": "who's", "lets": "let's", "theyre": "they're",
    "theyve": "they've", "theyll": "they'll", "weve": "we've", "gonna": "gonna",
}

# Words too common to be a useful standalone search fallback.
_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "in", "is", "me", "my", "your", "you",
    "with", "on", "no", "for", "it", "we", "he", "she", "at", "by", "as", "so",
    "i", "or", "if", "up", "vs", "was", "are", "not", "but", "who", "how",
}

_MAX_FALLBACK_QUERIES = 3


def _norm(s: str) -> str:
    """Fold to lowercase alphanumerics + single spaces, so ``Don't`` and
    ``dont`` compare equal when ranking."""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _restore_contractions(query: str) -> str | None:
    """Return the query with apostrophes restored (``dont`` -> ``don't``), or
    None if nothing changed."""
    out, changed = [], False
    for word in query.split():
        fixed = _CONTRACTIONS.get(word.lower())
        if fixed and fixed != word.lower():
            out.append(fixed)
            changed = True
        else:
            out.append(word)
    restored = " ".join(out)
    return restored if changed and _norm(restored) != _norm(query) else None


def _distinctive_words(query: str, *, limit: int = 2) -> list[str]:
    """The longest non-stopword tokens — searching one of these rescues a
    multi-word query with a typo elsewhere (``atack on titan`` -> ``titan``)."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", query.lower())
              if len(t) >= 4 and t not in _STOPWORDS]
    # Only useful when the query has more than one word to fall back through.
    if len(query.split()) < 2:
        return []
    return sorted(set(tokens), key=len, reverse=True)[:limit]


def _score(anime: Anime, norm_query: str) -> float:
    """Best fuzzy ratio of the query against any of the show's titles."""
    t = anime.title
    candidates = [t.romaji, t.english, t.native, *t.synonyms]
    return max(
        (SequenceMatcher(None, norm_query, _norm(c)).ratio() for c in candidates if c),
        default=0.0,
    )


def _merge(*groups: list[Anime]) -> list[Anime]:
    """Concatenate result groups, first occurrence wins (preserves AniList's
    relevance order within each group)."""
    seen: set[str] = set()
    out: list[Anime] = []
    for group in groups:
        for a in group:
            if a.id.key not in seen:
                seen.add(a.id.key)
                out.append(a)
    return out


class SearchService:
    def __init__(self, metadata: MetadataSource) -> None:
        self._metadata = metadata

    async def search(self, query: str, *, limit: int = 20) -> list[SearchResult]:
        animes = await self._smart_search(query, limit=limit)
        return [SearchResult(anime=a) for a in animes]

    async def best_match(self, query: str) -> Anime | None:
        results = await self._smart_search(query, limit=5)
        return results[0] if results else None

    async def get(self, anime_id: AnimeId) -> Anime:
        return await self._metadata.get(anime_id)

    # -- forgiving search --------------------------------------------------- #
    async def _smart_search(self, query: str, *, limit: int) -> list[Anime]:
        primary = await self._metadata.search(query, limit=limit)
        # Fast path: AniList found something → trust its relevance order, no
        # extra requests, no reordering. Nothing regresses for a good query.
        if primary or not query.strip():
            return primary

        # AniList came back empty — the query needs help. Build a few targeted
        # retries and fuzzy-rank whatever they turn up against the raw query.
        extra_queries: list[str] = []
        restored = _restore_contractions(query)
        if restored:
            extra_queries.append(restored)
        extra_queries.extend(_distinctive_words(query))
        extra_queries = extra_queries[:_MAX_FALLBACK_QUERIES]
        if not extra_queries:
            return primary

        groups = await asyncio.gather(
            *(self._metadata.search(q, limit=limit) for q in extra_queries)
        )
        pool = _merge(*groups)
        if not pool:
            return primary
        norm_query = _norm(query)
        pool.sort(key=lambda a: _score(a, norm_query), reverse=True)
        return pool[:limit]
