# Writing an anime-sh plugin

anime-sh discovers **providers** (where to find episodes) and **resolvers** (how
to turn a host embed into a playable stream) through Python **entry points**. A
plugin is just a small package that declares one. `pip install` it, restart, and
it works — no changes to anime-sh itself. A plugin that fails to load is logged
and skipped, never fatal.

Everything below talks only to the stable contracts in `anime_sh.domain.models`
and `anime_sh.domain.ports`. You never import the app internals.

## A provider in full

A provider maps a known `Anime` (keyed by its AniList id) to your site's show,
lists its episodes, and returns stream *candidates* (embed URLs). It must not
return playable video URLs — that's a resolver's job.

```python
# anime_provider_example/__init__.py
from anime_sh.domain.models import (
    Anime, AnimeId, Audio, Episode, ProviderRef, StreamCandidate,
)


class ExampleProvider:
    name = "example"
    priority = 50          # higher = tried earlier in the fan-out
    api_version = 1        # must match anime_sh.domain.ports.API_VERSION

    def __init__(self, http=None):
        # Construct your own HTTP client lazily; do NOT do I/O here.
        self._http = http

    async def match(self, anime: Anime, audio: Audio) -> ProviderRef | None:
        # Search your site for anime.title.romaji / .english, pick the best
        # match, and return a ref carrying your site's show id. Return None if
        # the show isn't there (that is not an error).
        show_id = await self._find(anime)
        if show_id is None:
            return None
        return ProviderRef(provider=self.name, anime_key=show_id, audio=audio)

    async def episodes(self, ref: ProviderRef, anime_id: AnimeId) -> list[Episode]:
        # anime_id is passed in because you only know your own show id, not the
        # identity spine — stamp it onto each Episode.
        return [
            Episode(anime_id=anime_id, number=n, provider_ref=ref, episode_key=str(n))
            for n in await self._episode_numbers(ref.anime_key, ref.audio)
        ]

    async def candidates(self, episode: Episode) -> list[StreamCandidate]:
        return [
            StreamCandidate(host="somehost", url=embed_url, audio=episode.provider_ref.audio)
            for embed_url in await self._embeds(episode)
        ]
```

Register it in the plugin package's `pyproject.toml`:

```toml
[project.entry-points."anime_sh.providers"]
example = "anime_provider_example:ExampleProvider"
```

## A resolver

A resolver knows a host, never an anime. It turns one candidate into playable
`Stream`s.

```python
from anime_sh.domain.models import Stream, StreamCandidate, StreamKind, Quality


class SomehostResolver:
    name = "somehost"
    api_version = 1

    def handles(self, candidate: StreamCandidate) -> bool:
        return "somehost.tld" in candidate.url

    async def resolve(self, candidate: StreamCandidate) -> list[Stream]:
        m3u8 = await self._extract(candidate.url)
        return [Stream(url=m3u8, kind=StreamKind.HLS, quality=Quality.UNKNOWN,
                       headers={"Referer": "https://somehost.tld/"})]
```

```toml
[project.entry-points."anime_sh.resolvers"]
somehost = "anime_resolver_somehost:SomehostResolver"
```

## Rules that keep plugins interchangeable

- **`api_version` must equal `anime_sh.domain.ports.API_VERSION`.** A mismatch is
  refused with a clear message rather than half-loaded.
- **`__init__` must not do I/O.** Construct clients lazily so discovery is fast
  and offline.
- **Return domain models, never dicts.** `match`/`episodes`/`candidates` and
  `handles`/`resolve` are the whole contract.
- **Degrade, don't crash.** Raise `anime_sh.domain.errors.ProviderError` /
  `ResolverError` for expected failures; the manager skips you and moves on.

## Verifying

anime-sh ships a registry-wide contract suite (`tests/contract/`) that every
installed plugin is held to automatically — async signatures, arity,
`api_version`, unique names. Install your plugin into the same environment and
run `uv run pytest tests/contract` to confirm it conforms. Add a recorded
fixture test for your parser and a gated live test (see the bundled providers).

Check your plugin is discovered:

```bash
anime providers ls        # or: anime providers health
```
