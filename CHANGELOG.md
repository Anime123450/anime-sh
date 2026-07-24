# Changelog

All notable changes to anime-sh. Format loosely follows Keep a Changelog.

## [0.2.0]

Discovery, reliability, and a search that understands what you meant — the first
release intended for PyPI.

### Added

- **Forgiving search** — when AniList's strict search returns nothing, retry with
  apostrophes restored (`dont` → `don't`) and the query's distinctive words, then
  fuzzy-rank the results against what you typed. `atack on titan` now finds it.
- **Discovery** — `anime recommend "<title>"` (AniList recommendations) and
  `anime related "<title>"` (prequels, sequels, side stories, movies).
- **Universal intro/outro skip** — AniSkip fills op/ed timestamps in when a
  provider ships none, so auto-skip works on every source.
- **Batch/season downloads** — `anime download -e 1-12` (and `1,3,5`), resumable
  (skips episodes already on disk) and robust to a single-episode failure.
- **Cached catalog** — AniList responses cached in a disposable `cache.db`;
  repeat browses are instant and recently-seen pages render offline.
  `anime cache clear` / `purge`.
- **Third provider** — AniZone (clean, un-obfuscated HLS with soft English subs).
- `providers.preferred` now orders the fan-out; `anime --version`; shell
  completion (`anime --install-completion`).

### Changed

- **Faster, more reliable playback** — a provider's candidate hosts are resolved
  concurrently (a slow/dead host no longer blocks the rest), and each resolved
  stream is pre-flighted so a dead CDN is dropped before the player is launched.
- **Reliable `--dub`** — AniZone (sub-only) no longer shadows dub requests, so the
  fan-out reaches a dub-capable provider.
- Browse commands (`trending`/`seasonal`/`calendar`) degrade gracefully instead
  of dumping a traceback when AniList is unreachable.
- Dropped config settings that were never wired (`resolvers.preferred_hosts`,
  `[tracking]`, `downloads.concurrency`).

### Fixed

- anikoto playback: de-obfuscation keys off the resolver, not a rotating CDN
  hostname, so it survives the CDN moving (nekostream → kotocdn → …).
- AllAnime: restored stream discovery after the mkissa.to crypto rewrite.

## [0.1.0] — unreleased

First end-to-end release. The full path — search → provider fan-out → resolver
fallback → mpv — works, with two live providers and a keyboard-driven TUI.

### Added

- **Domain core** — immutable models keyed on the AniList identity spine,
  `Protocol` ports, pure ranking, and a pure circuit-breaker state machine.
  Layering (`cli > tui > app > domain`) enforced in CI by import-linter.
- **Metadata** — AniList GraphQL source: search, trending, seasonal, airing
  schedule.
- **Providers** — AllAnime (ani-cli protocol: persisted-query + AES-CTR
  `tobeparsed` + XOR) and anikoto (HiAnime-family), both discovered via entry
  points. Parallel fan-out with per-provider timeouts and **persisted circuit
  breakers** + health-based reordering.
- **Resolvers** — AllAnime clock, mp4upload, the megaplay family
  (vidtube/megaplay.buzz/vidwish), and a generic HLS/MP4 passthrough, tried as a
  fallback chain.
- **Player** — mpv over JSON IPC (Windows named-pipe / Unix socket), with resume,
  automatic intro/outro skip, and auto-play-next.
- **Library** — SQLite, split into a sacred store (progress, history, favorites,
  cached metadata) and a disposable cache; numbered migrations from day one.
- **Downloads** — `anime download` via ffmpeg (stream copy) with DB tracking.
- **TUI** — bare `anime` launches a Textual app: search-as-you-type,
  continue-watching, trending → episodes → play.
- **CLI** — `search`, `play`, `trending`, `seasonal`, `calendar`, `random`,
  `continue`, `resume`, `history`, `favorite`, `download`, `downloads`,
  `providers`, `config`, `doctor` — all scriptable with `--json`.
- **Ops** — a nightly canary that probes each provider, publishes
  `provider-status.json`, and files a deduped issue on breakage; a registry-wide
  plugin contract suite; a PyPI trusted-publishing release workflow.

### Known limitations

- Some streaming hosts actively obstruct downloads (cross-origin segment
  redirects that strip the referer); those play but don't download.
- AniList write-sync and Discord Rich Presence are stubs for a later release.
