# Changelog

All notable changes to anime-sh. Format loosely follows Keep a Changelog.

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
