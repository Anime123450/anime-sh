# anime-sh architecture

anime-sh is a terminal-native anime client, not a scraper. The user types a
title and gets a playing episode; providers, mirrors, and resolvers are
internal details they never see.

## The layers

```
cli / tui         adapters — no domain logic, one call each into app services
   │
app (services)    orchestration only: PlaybackService, ProviderManager, …
   │
domain            stable core: frozen models + ports (Protocols) + ranking
   │
infra / providers / resolvers   swappable adapters behind the ports
```

Dependencies point **downward only**, enforced in CI by `import-linter`
(`lint-imports`):

- `domain/` imports nothing else in the package. It is pure data + contracts.
- `app/` imports only `domain`. It never touches an HTTP client, a database,
  or a concrete provider — only the ports in `domain/ports.py`.
- The composition root (`cli/container.py`) is the single place that knows both
  `app` and `infra`, and wires concretes to services.

If a change makes `app` want to import `infra`, that is the architecture telling
you a new port is missing.

## The identity spine

Every `Anime` is keyed by an `AnimeId` (AniList id primary). Metadata — search,
trending, seasonal, calendar — comes from AniList, **not** from providers.
Providers are *sources* attached to a known identity via `provider_map`
(`anilist_id → provider-native key`), cached forever once found.

This makes cross-provider fan-out a dict lookup instead of fuzzy title matching,
and it keeps history/favorites meaningful even when every provider is down.

## The money path (`PlaybackService`)

1. Look up resume position (`Library`, SQLite).
2. Fan out to providers to map the identity → `ProviderRef`s (`ProviderManager`,
   per-provider timeouts + guards; a dead provider degrades, never crashes).
3. For each provider in priority order: list episodes, find the requested one,
   get ordered `StreamCandidate`s (embed pages, not videos).
4. **Fallback chain** (`_resolve_stream`): for each candidate host, try each
   capable `Resolver`; the first host that yields streams wins. A failing or
   crashing resolver is skipped silently. Exhaustion → `NoStreamsFound`.
5. Pick quality (`domain/ranking.py`, pure), hand the `Stream` to a `Player`.

`StreamCandidate` (what a provider returns) vs `Stream` (what a resolver
returns) is the seam that keeps "providers don't know URLs, resolvers don't know
anime" real and testable.

## Persistence

Two physically separate SQLite files:

- `anime.db` — **sacred** user state (progress, favorites, history, provider
  mappings, circuit-breaker state). Never auto-expired.
- `cache.db` — **disposable** (episode lists, search/trending, candidate lists).
  `anime cache clear` can only ever touch this one.

Numbered SQL migrations run on startup and are idempotent (`schema_version`
table). Resolved stream URLs are never cached — they are IP/time-bound.

## Plugins

Providers and resolvers are discovered via Python entry points
(`anime_sh.providers`, `anime_sh.resolvers`). `pip install anime-provider-foo`
→ restart → it works. Bundled and third-party plugins use the same path. A bad
plugin is logged and skipped, never fatal; a plugin built against the wrong
`api_version` is refused with a clear message.

## Status: M1 (vertical slice)

Real adapters now run through every layer:

- **Metadata**: `AniListMetadata` (GraphQL) — search, get, trending, seasonal,
  airing schedule. Verified live.
- **HTTP**: `HttpClient` with an httpx backend plus an optional `curl_cffi`
  browser-impersonation backend; detects Cloudflare interstitials and raises
  `CloudflareChallenge` (we do not attempt to solve them).
- **Provider**: `AllAnimeProvider` (match / episodes / candidates, with the
  XOR source-URL decoder). Discovered via entry points.
- **Resolvers**: `AllAnimeClockResolver` (internal clock endpoint) and a
  `GenericStreamResolver` passthrough for direct media URLs.
- **Player**: `MpvPlayer` over JSON IPC, with the Windows named-pipe vs Unix
  socket transport abstracted behind a reader thread. Verified live end-to-end
  (real mpv + real SQLite progress) in `tests/integration/test_mpv_playback.py`.
- **CLI**: `anime search`, `anime trending`, `anime play` / `anime <query>`
  sugar, each with `--json`. Progress is tracked and persisted (throttled).

Known limitation: AllAnime sits behind Cloudflare's managed challenge on many
networks. When challenged, the provider degrades cleanly (the manager skips it)
and playback reports "no provider has …" — exactly the designed-for state. This
is why the architecture never binds identity or the catalog to any provider.

Next: **M2** — history, `continue watching`, favorites; then **M3** plurality
(more providers, circuit breakers, nightly canary).
