"""SearchService — forgiving orchestration over the metadata source.

Search touches only the metadata source (AniList), never a scraper: it is
instant and reliable, and provider availability is resolved lazily later, at
play time. That is what keeps search-as-you-type fast in the TUI.

AniList's own search is strict, whole-word matching: it wants each word spelled
the way it stores it. Substrings and fragments find nothing (``fri`` misses
*Frieren*), de-spaced titles miss (``onepiece`` misses *One Piece*), a stray
typo kills the whole query (``atack on titan`` -> nothing), and English
stopwords are dropped from its index entirely (``the`` -> nothing). None of that
is how search in a modern anime client should feel.

So this service layers a **local index** over AniList without slowing the fast
path:

* A working query costs exactly one request — no extra lookups, no index build.
  Its results are re-ranked locally against what was typed, because AniList's
  own order is not enough on its own: it put a soft-drink commercial above the
  film for "Your Name" and a one-off short above the series for "JoJo". Ranking
  blends match strength, exactness and popularity (see :func:`_ranked`); it is
  pure CPU over at most a screenful of rows, so the fast path stays fast.
* Only when AniList returns **nothing** do we escalate: retry a few normalised
  variants (apostrophes restored, punctuation/camelCase de-glued, the query's
  distinctive words) *and* substring/prefix/fuzzy-match the query against a
  cached snapshot of the most popular anime. The merged pool is ranked against
  what the user actually typed.

The index is a disposable, day-cached popularity snapshot from the metadata
source, built lazily on the first query that needs it. If it can't be built
(offline, or the source doesn't expose ``popular``) search degrades cleanly to
AniList's strict behaviour.
"""

from __future__ import annotations

import asyncio
import math
import re
import unicodedata
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

# How many of the most popular anime to hold in the local index. Wide enough to
# cover essentially every streamable show, small enough to fetch in one burst
# and match against per-keystroke without noticeable cost.
_INDEX_SIZE = 500

# Minimum match strength for an index entry to count as a hit for the query.
# Prefix / substring / squashed-equality matches all score well above this; the
# floor mainly admits close typo matches and rejects incidental fuzz.
_INDEX_MATCH_THRESHOLD = 0.5


def _fold(s: str) -> str:
    """Lowercase after NFKC folding, so fullwidth text (ＮＡＲＵＴＯ) and the
    Unicode roman numerals (Ⅱ) compare as their ASCII equivalents instead of
    being stripped out entirely."""
    return unicodedata.normalize("NFKC", s).lower()


def _norm(s: str) -> str:
    """Fold to lowercase alphanumerics + single spaces, so ``Don't`` and
    ``dont`` compare equal when ranking."""
    return re.sub(r"[^a-z0-9]+", " ", _fold(s)).strip()


def _squash(s: str) -> str:
    """Fold to lowercase alphanumerics with *no* separators, so spacing and
    punctuation stop mattering (``One Piece`` / ``one-piece`` / ``onepiece`` all
    become ``onepiece``)."""
    return re.sub(r"[^a-z0-9]+", "", _fold(s))


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


def _despace(query: str) -> str | None:
    """Split camelCase and turn punctuation into spaces (``ReZero`` -> ``Re
    Zero``, ``Dr.Stone`` -> ``Dr Stone``), or None if that changes nothing —
    rescues glued spellings AniList would otherwise treat as one dead token."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", query)
    spaced = re.sub(r"[^0-9A-Za-z]+", " ", spaced)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    return spaced if spaced and _norm(spaced) != _norm(query) else None


def _distinctive_words(query: str, *, limit: int = 2) -> list[str]:
    """The longest non-stopword tokens — searching one of these rescues a
    multi-word query with a typo elsewhere (``atack on titan`` -> ``titan``)."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", query.lower())
              if len(t) >= 4 and t not in _STOPWORDS]
    # Only useful when the query has more than one word to fall back through.
    if len(query.split()) < 2:
        return []
    return sorted(set(tokens), key=len, reverse=True)[:limit]


def _fallback_queries(query: str) -> list[str]:
    """The normalised retry variants to try against AniList when the raw query
    comes back empty — de-duplicated and capped."""
    candidates: list[str] = []
    restored = _restore_contractions(query)
    if restored:
        candidates.append(restored)
    despaced = _despace(query)
    if despaced:
        candidates.append(despaced)
    candidates.extend(_distinctive_words(query))

    seen: set[str] = set()
    unique: list[str] = []
    for q in candidates:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:_MAX_FALLBACK_QUERIES]


def _rank_score(anime: Anime, norm_query: str, squash_query: str) -> float:
    """How well the query matches any of the show's titles, on a 0–1 scale that
    rewards (in order) an exact squashed match, a title that *starts with* the
    query, a word that starts with the query's first word, the query appearing
    as a substring, and otherwise raw fuzzy similarity."""
    if not norm_query:
        return 0.0
    first = norm_query.split()[0]
    t = anime.title
    best = 0.0
    primary = (t.romaji, t.english, t.native)
    for c in (*primary, *t.synonyms):
        if not c:
            continue
        # Synonyms are crowd-sourced aliases, not the show's name, and they are
        # noisy: an 89-popularity short listing "JoJo" as an alias outscored
        # JoJo's Bizarre Adventure, whose *title* merely starts with it. Discount
        # them just below a prefix match on a real title.
        weight = 1.0 if c in primary else 0.94
        nt = _norm(c)
        if not nt:
            continue
        st = _squash(c)
        s = SequenceMatcher(None, norm_query, nt).ratio()
        if squash_query and st == squash_query:
            s = max(s, 1.0)
        elif squash_query and st.startswith(squash_query):
            s = max(s, 0.95)
        elif squash_query and squash_query in st:
            s = max(s, 0.75)
        if any(w.startswith(first) for w in nt.split()):
            s = max(s, 0.85)
        s *= weight
        if s > best:
            best = s
    return best


def _ranked(pool: list[Anime], query: str, limit: int) -> list[Anime]:
    """Order candidates by how well they match, popularity breaking ties.

    Ties are common and meaningful: an exact-alias match on an obscure tie-in
    commercial and one on the famous film both score 1.0, and only popularity
    tells them apart. Python's sort is stable, so anything still tied keeps the
    order the metadata source gave it.
    """
    if not pool:
        return pool
    norm_query, squash_query = _norm(query), _squash(query)
    if not norm_query:
        return pool[:limit]
    def key(a: Anime) -> float:
        # One blended score rather than strict tiers. Exactness has to outrank
        # popularity — "Nisekoi:" and "Nisekoi" differ only by the colon, and the
        # colon is the whole difference between two seasons. But it can't outrank
        # it *unconditionally*: an 89-popularity short whose romaji title is
        # literally "JoJo" would then bury JoJo's Bizarre Adventure. Both are
        # bounded contributions, so a huge popularity gap can overcome a small
        # exactness edge and nothing else can.
        return (
            _rank_score(a, norm_query, squash_query)
            + _EXACTNESS_WEIGHT * _exactly_titled(a, query, norm_query)
            + _POPULARITY_WEIGHT * _popularity_factor(a.popularity)
        )

    return sorted(pool, key=key, reverse=True)[:limit]


# Tuned against 500 real titles plus the known-bad queries; see the tests.
_EXACTNESS_WEIGHT = 0.05
_POPULARITY_WEIGHT = 0.25


def _popularity_factor(popularity: int | None) -> float:
    """Popularity on a 0–1 curve. Log-scaled because AniList popularity spans
    five orders of magnitude, and the difference between 90 and 900 should not
    count for as much as the difference between 90 and 400,000."""
    if not popularity or popularity <= 0:
        return 0.0
    return min(math.log10(popularity) / 6.0, 1.0)


def _exactly_titled(anime: Anime, query: str, norm_query: str) -> int:
    """2 for a character-for-character title, 1 for one that only differs in
    punctuation or spacing, 0 otherwise.

    The two levels matter because folding is what makes "Kaguya-sama: Love is
    War?" and "Kaguya-sama: Love is War" indistinguishable — the trailing "?" is
    the entire difference between two seasons.
    """
    raw = _fold(query).strip()
    titles = (anime.title.english, anime.title.romaji, anime.title.native,
              *anime.title.synonyms)
    if any(c and _fold(c).strip() == raw for c in titles):
        return 2
    return int(any(c and _norm(c) == norm_query for c in titles))


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
        self._index: list[Anime] | None = None
        self._index_lock = asyncio.Lock()

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
        # Fast path: AniList found something → no extra requests, no index build.
        # Its ordering alone is not enough though: searching "Your Name" put a
        # soft-drink commercial above the film, and "JoJo" put a one-off short
        # above the series. Re-rank what we already have — same scoring the
        # fallback path uses, pure CPU on ≤20 rows, so the fast path stays fast.
        if primary or not query.strip():
            return _ranked(primary, query, limit)

        # AniList came back empty — the query needs help. Fire a few targeted
        # AniList retries and match against the local popularity index, then
        # fuzzy-rank the whole pool against what the user actually typed.
        norm_query, squash_query = _norm(query), _squash(query)

        variants = _fallback_queries(query)
        groups: list[list[Anime]] = []
        if variants:
            groups = list(await asyncio.gather(
                *(self._metadata.search(v, limit=limit) for v in variants)
            ))

        index = await self._catalog()
        index_hits = [
            a for a in index
            if _rank_score(a, norm_query, squash_query) >= _INDEX_MATCH_THRESHOLD
        ]

        pool = _merge(*groups, index_hits)
        return _ranked(pool, query, limit)

    async def _catalog(self) -> list[Anime]:
        """The local popularity index, built once (per process) on first need.
        Best-effort: any failure yields an empty index so search still works."""
        if self._index is not None:
            return self._index
        popular = getattr(self._metadata, "popular", None)
        if popular is None:
            self._index = []
            return self._index
        async with self._index_lock:
            if self._index is None:
                try:
                    self._index = await popular(limit=_INDEX_SIZE)
                except Exception:
                    self._index = []
        return self._index
