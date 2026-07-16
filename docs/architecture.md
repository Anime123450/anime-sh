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

## Status: M5 (polish)

The money path is now hands-off, and downloads landed.

- **Auto intro/outro skip** — the provider skip data (anikoto's server response,
  megaplay's `getSources`) flows through as `Stream.skip_times`; the playback
  loop seeks past the OP/ED when position enters the range (gated by
  `playback.skip_intro`/`skip_outro`).
- **Auto-play-next** — on natural EOF (mpv end-file reason `eof`) of a
  watched-to-completion episode, `play_and_track` rolls straight into the next
  one, until the season ends or you quit mpv. Quitting early (reason `quit`) or
  an unfinished episode does *not* advance. Works in both CLI and TUI.
- **Downloads** — `anime download <title> -e N` resolves through the same
  provider fan-out and saves via ffmpeg (`DownloadService` + `FfmpegDownloader`,
  stream-copy, propagating `-referer`/`-user_agent`, `-extension_picky 0`);
  tracked in the `downloads` table and listed by `anime downloads`. Verified
  against well-behaved HLS. Deliberately hostile CDNs (cross-origin segment
  redirects that strip the referer) fail with an honest error — same "hosts are
  flaky" reality as playback.

## Status: M4 (TUI)

Bare `anime` now launches a Textual TUI — the second adapter onto the app
services, holding no domain logic of its own.

- **Home** (`tui/screens/home.py`): search-as-you-type (debounced, hits only the
  metadata source), plus Continue Watching and Trending lists.
- **Detail** (`tui/screens/detail.py`): metadata + an episode list built from
  AniList's episode count (instant — providers are consulted only when you press
  Enter to play, which fans out through `PlaybackService`). Playback failures
  surface as a toast; the TUI never crashes on a dead provider.
- Keyboard-driven (`/` search, Enter select, Esc back, `q` quit), themed via the
  config `ui.theme` (Textual built-ins, default tokyo-night).

Layering stays honest: the TUI receives its services by injection from the
composition root (the CLI), so it imports only `app` services and `domain`
ports — never the CLI or a concrete infra adapter. The import-linter contract is
now `cli > tui > app > domain`.

Verified headlessly with Textual's `Pilot` (`tests/tui/`) — home population,
search swap-in, navigation, and episode→playback — and smoke-tested against live
AniList (trending + search return real data).

## Status: M3 (plurality)

The fan-out is now real, resilient, and self-monitoring.

**Second provider** — `AnikotoProvider` (anikototv.to, a HiAnime-family site):
search → `/ajax/episode/list` → `/ajax/server/list` → `/ajax/server`, parsed
offline; carries per-episode MAL ids and sub/dub availability. Its
`MegaplayResolver` (`resolvers/vidtube`) resolves anikoto's rotating
megaplay-clone hosts (vidtube.site / megaplay.buzz / vidwish.live): read the
player `cidu` from the embed page, then `getSources?id=<cidu>` (AJAX-only)
returns a plaintext m3u8 + subtitles + skip times. Verified live end-to-end:
`Smoking Behind the Supermarket with You` — which AllAnime only has a 1-episode
"mini" of — matches on anikoto, resolves to a real `.m3u8`, and plays in mpv.

**Circuit breakers** (`domain/health.py`, pure) — a provider that fails
`threshold` times in a row trips OPEN for a cooldown; the `ProviderManager` skips
it and stops paying its timeout. After the cooldown, one half-open probe
(derived from OPEN + elapsed time) either closes it or re-opens it. State is
persisted in `provider_health` (via `SqliteHealthStore`) so it survives
restarts. Only match timeouts/errors count against the breaker — a provider that
responds but has no match for a title is still healthy.

**Health-based reordering** — the manager tries providers healthiest-first
(closed → half-open → open), then by priority, and drops open ones from the
fan-out entirely. `anime providers health` shows the live breaker table.

**Contract tests** (`tests/contract/`) — a registry-parametrized suite every
provider/resolver must pass (async signatures, `api_version`, structural
conformance, unique names), so bundled and third-party plugins are held to the
same contract automatically.

**Nightly canary** (`scripts/canary.py` + `.github/workflows/canary.yml`) —
runs each provider's read path against the real site, writes
`provider-status.json`, and opens/updates (deduped) a GitHub issue when a
provider breaks. It distinguishes a broken *provider* (no candidates) from
flaky *hosts* (candidates OK but nothing resolves) — only the former fails.

Deferred: a third provider; client-side consumption of `provider-status.json`.

## Status: M2 (persistence & resume)

On top of M1, the library is now a first-class store:

- `SqliteLibrary` gained a metadata cache (the `anime` table), favorites,
  history, and continue-watching — all joined to cached metadata so they render
  offline. A LEFT-JOIN miss falls back to a placeholder title, never a crash.
- `LibraryService` orchestrates favorites/history/resume; `PlaybackService`
  caches the show and records a history row per play session.
- CLI: `anime continue`, `anime resume`, `anime history`,
  `anime favorite add|rm|ls` — all with `--json`.
- Verified end-to-end (real mpv + real SQLite) in the live integration test:
  a play populates progress, history, the metadata cache, and continue-watching.

### M1 (vertical slice)

Real adapters now run through every layer:

- **Metadata**: `AniListMetadata` (GraphQL) — search, get, trending, seasonal,
  airing schedule. Verified live.
- **HTTP**: `HttpClient` with an httpx backend plus an optional `curl_cffi`
  browser-impersonation backend; detects Cloudflare interstitials and raises
  `CloudflareChallenge` (we do not attempt to solve them).
- **Provider**: `AllAnimeProvider`, ported faithfully from ani-cli — POST
  GraphQL with Referer/Origin `youtu-chan.com` and a Firefox UA (this is what
  clears the Cloudflare edge), a persisted-query GET for episode sources, an
  AES-256-CTR `tobeparsed` decrypt, and the XOR source-URL decode. Discovered
  via entry points. **Verified live**: it matches, lists episodes, and decodes
  real per-host stream candidates (`tests/integration/test_allanime_live.py`).
- **Resolvers**: `AllAnimeClockResolver` (AllAnime's internal clock endpoint on
  `allanime.day`), `Mp4UploadResolver`, and a `GenericStreamResolver`
  passthrough for direct media URLs.
- **Player**: `MpvPlayer` over JSON IPC, with the Windows named-pipe vs Unix
  socket transport abstracted behind a reader thread. Verified live end-to-end
  (real mpv + real SQLite progress) in `tests/integration/test_mpv_playback.py`.
- **CLI**: `anime search`, `anime trending`, `anime play` / `anime <query>`
  sugar, each with `--json`. Progress is tracked and persisted (throttled).

Known limitation (per-host, not per-provider): the AllAnime *provider* is
reliable, but individual video hosts behind it (its internal clock, mp4upload,
streamwish, …) are frequently down, geo-blocked, or throttled. The resolver
fallback chain tries each host in turn and moves on; adding more host resolvers
is ongoing, incremental work — the same reality ani-cli lives with. If every
host for an episode is unreachable, playback fails with an honest message rather
than a crash.

Next: **M3** plurality — more providers (AnimeKai / HiAnime / AnimePahe),
circuit breakers persisted in `provider_health`, health-based reordering, a
contract-test suite over the registry, and a nightly canary.
